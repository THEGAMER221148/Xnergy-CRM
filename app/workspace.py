"""Personal calendar and internal workspace. Tokens never leave server-side Redis."""
import json,time,re
from uuid import UUID
from datetime import datetime,timedelta,timezone,date
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pathlib import Path
from urllib.parse import urlparse,quote
import httpx
from fastapi import APIRouter,Request,HTTPException,Query
from pydantic import BaseModel,Field,field_validator,model_validator
from typing import Literal
from . import security
from .config import get_settings
from .db import tenant_tx
router=APIRouter()
ROLES=['admin','banker','deal_manager','compliance']
SCOPES='openid profile email offline_access https://graph.microsoft.com/Calendars.ReadBasic'
def key(u):return f"calendar:{u['tenant_id']}:{u['user_id']}"

@router.get('/auth/calendar/connect')
async def connect(request:Request,mode:Literal['read','manage']='read',next:str='/static/workspace/today.html?calendar=connected'):
    u=await security.require_user(request,ROLES)
    destination=next if next.split('?')[0] in ('/static/workspace/today.html','/static/workspace/calendar.html') else '/static/workspace/calendar.html'
    scopes=SCOPES if mode=='read' else 'openid profile email offline_access https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/Calendars.ReadWrite.Shared'
    return await security.oidc_login(destination,{'tenant_id':u['tenant_id'],'user_id':u['user_id'],'scopes':scopes})

async def save_calendar_connection(current,row,connection):
    expected=connection['user']
    if any(str(current[k])!=str(expected[k]) for k in ('tenant_id','user_id')) or str(row['id'])!=str(current['user_id']) or str(row['tenant_id'])!=str(current['tenant_id']):
        raise HTTPException(403,'Connect the same Microsoft account you use for DealOS.')
    t=connection['tokens']
    if not t.get('access_token') or not any(x.rsplit('/',1)[-1] in ('Calendars.ReadBasic','Calendars.Read','Calendars.ReadWrite','Calendars.ReadWrite.Shared') for x in t.get('scope','').split()):
        raise HTTPException(403,'Calendar read permission was not granted. Please connect again.')
    await security._redis().setex(key(current),28*86400,json.dumps({'access_token':t['access_token'],'refresh_token':t.get('refresh_token'),'expires_at':time.time()+int(t.get('expires_in',3600)),'scope':t.get('scope',''),'requested_scopes':connection['user'].get('scopes',SCOPES)}))

async def calendar_token(u):
    raw=await security._redis().get(key(u))
    if not raw:return None
    t=json.loads(raw)
    if t['expires_at']>time.time()+60:return t['access_token']
    if not t.get('refresh_token'):return None
    s=get_settings();d=await security.discovery()
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.post(d['token_endpoint'],data={'grant_type':'refresh_token','refresh_token':t['refresh_token'],'client_id':s.oidc_client_id,'client_secret':s.oidc_client_secret,'scope':t.get('requested_scopes',SCOPES)})
    if r.status_code in (400,401):
        await security._redis().delete(key(u));return None
    r.raise_for_status();new=r.json()
    t['scope']=new.get('scope',t.get('scope',''))
    t.update(access_token=new['access_token'],refresh_token=new.get('refresh_token',t['refresh_token']),expires_at=time.time()+int(new.get('expires_in',3600)))
    await security._redis().setex(key(u),28*86400,json.dumps(t))
    return t['access_token']

@router.post('/api/calendar/disconnect')
async def disconnect(request:Request):
    u=await security.require_user(request,ROLES);await security.enforce_csrf(request,u)
    await security._redis().delete(key(u));return {'ok':True}

@router.get('/api/calendar/events')
async def events(request:Request,tz:str=Query('America/Los_Angeles',max_length=80),start_date:date|None=None,end_date:date|None=None,calendar_id:str=''):
    u=await security.require_user(request,ROLES)
    await security.rate_limit('calendar-read:'+u['user_id'],30,60)
    try:zone=ZoneInfo(tz)
    except ZoneInfoNotFoundError:raise HTTPException(422,'Unknown time zone')
    if bool(start_date)!=bool(end_date):raise HTTPException(422,'Provide both start and end dates.')
    start=datetime.combine(start_date,datetime.min.time(),zone) if start_date else datetime.now(zone).replace(hour=0,minute=0,second=0,microsecond=0)
    end=datetime.combine(end_date,datetime.min.time(),zone) if end_date else start+timedelta(days=1)
    if not timedelta(0)<end-start<=timedelta(days=62):raise HTTPException(422,'Choose a calendar range of 1 to 62 days.')
    if len(calendar_id)>1024:raise HTTPException(422,'Invalid calendar')
    try:
        token=await calendar_token(u)
        if not token:return {'connected':False,'events':[]}
        params={'startDateTime':start.isoformat(),'endDateTime':end.isoformat(),'$select':'id,iCalUId,subject,start,end,isAllDay,isCancelled,location,onlineMeeting,webLink,organizer,attendees,sensitivity','$orderby':'start/dateTime','$top':'100'}
        url='https://graph.microsoft.com/v1.0/me/'+('calendars/'+quote(calendar_id,safe='')+'/' if calendar_id else '')+'calendarView';items=[]
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(10):
                r=await client.get(url,params=params,headers={'Authorization':'Bearer '+token,'Prefer':'outlook.timezone="UTC"'})
                if r.status_code==401:
                    await security._redis().delete(key(u));return {'connected':False,'events':[]}
                if r.status_code==403:raise HTTPException(403,'Microsoft did not allow calendar access. Reconnect or ask your Microsoft 365 administrator to approve calendar read access.')
                if r.status_code==404:raise HTTPException(404,'No Microsoft 365 calendar mailbox was found for this account.')
                r.raise_for_status();data=r.json()
                items.extend(x for x in data.get('value',[]) if not x.get('isCancelled'))
                url=data.get('@odata.nextLink');params=None
                if not url:break
                parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.netloc!='graph.microsoft.com':raise HTTPException(502,'Unexpected calendar response')
        return {'connected':True,'events':items,'has_more':bool(url),'date':start.date().isoformat(),'time_zone':tz}
    except httpx.HTTPError:
        raise HTTPException(502,'Microsoft calendar is temporarily unavailable. Please refresh shortly.')

class Availability(BaseModel):
    status:Literal['Available','Busy','Away','Offline']
@router.post('/api/workspace/availability')
async def availability(request:Request,body:Availability):
    u=await security.require_user(request,ROLES);await security.enforce_csrf(request,u)
    await security._redis().setex(f"availability:{u['tenant_id']}:{u['user_id']}",86400,body.status)
    return {'status':body.status}

@router.get('/api/workspace/team')
async def team(request:Request):
    u=await security.require_user(request,ROLES)
    async with tenant_tx(u) as c:
        rows=[dict(r) for r in await c.fetch("select id,display_name,email,role from users where active and role<>'investor' order by display_name")]
    # Internal published directory enriches profiles without creating login accounts.
    published=json.loads(Path(__file__).with_name('team_directory.json').read_text()) if str(u['tenant_id'])==get_settings().workspace_directory_tenant_id else []
    merged={p['email'].lower():dict(p) for p in published}
    for row in rows:
        email=row['email'].lower();p=merged.setdefault(email,{})
        p.update(row);p['id']=str(p['id'])
        p['availability']=await security._redis().get(f"availability:{u['tenant_id']}:{p['id']}") or 'Not set'
    return {'people':sorted(merged.values(),key=lambda p:p['display_name'].lower()),'my_availability':await security._redis().get(f"availability:{u['tenant_id']}:{u['user_id']}") or 'Not set'}

def calendar_base(calendar_id=''):
    return 'https://graph.microsoft.com/v1.0/me/'+('calendars/'+quote(calendar_id,safe='') if calendar_id else 'calendar')

async def graph_result(response):
    if response.status_code==401:raise HTTPException(401,'Please reconnect your Microsoft calendar.')
    if response.status_code==403:raise HTTPException(403,'Microsoft has not granted this calendar permission. Enable scheduling, or ask the calendar owner to share access.')
    if response.status_code==404:raise HTTPException(404,'This calendar is unavailable or no longer shared with you.')
    if response.status_code==429:raise HTTPException(429,'Microsoft is busy. Please wait a moment and try again.')
    if response.status_code>=400:raise HTTPException(502,'Microsoft could not complete the calendar request. Check Outlook before retrying if you were saving a meeting.')
    return response.json()

@router.get('/api/calendar/calendars')
async def calendars(request:Request):
    u=await security.require_user(request,ROLES)
    await security.rate_limit('calendar-list:'+u['user_id'],30,60)
    try:
        token=await calendar_token(u)
        if not token:return {'connected':False,'calendars':[],'can_schedule':False}
        raw=await security._redis().get(key(u));stored=json.loads(raw) if raw else {}
        scopes={x.rsplit('/',1)[-1] for x in stored.get('scope','').split()}
        url='https://graph.microsoft.com/v1.0/me/calendars?$select=id,name,owner,canEdit,isDefaultCalendar,allowedOnlineMeetingProviders&$top=100';rows=[]
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(10):
                data=await graph_result(await client.get(url,headers={'Authorization':'Bearer '+token}))
                rows.extend(data.get('value',[]));url=data.get('@odata.nextLink')
                if not url:break
                if urlparse(url).scheme!='https' or urlparse(url).netloc!='graph.microsoft.com':raise HTTPException(502,'Unexpected calendar response')
        return {'connected':True,'calendars':rows,'has_more':bool(url),'can_schedule':bool(scopes&{'Calendars.ReadWrite','Calendars.ReadWrite.Shared'}),'shared_access':'Calendars.ReadWrite.Shared' in scopes}
    except httpx.HTTPError:raise HTTPException(502,'Microsoft calendar is temporarily unavailable.')

class Meeting(BaseModel):
    subject:str=Field(min_length=1,max_length=250)
    start:datetime
    end:datetime
    calendar_id:str=Field(default='',max_length=1024)
    attendees:list[str]=Field(default_factory=list,max_length=100)
    location:str=Field(default='',max_length=500)
    description:str=Field(default='',max_length=8000)
    meeting_url:str=Field(default='',max_length=2000)
    teams:bool=False
    transaction_id:UUID
    @field_validator('subject')
    @classmethod
    def subject_required(cls,v):
        if not v.strip():raise ValueError('Enter a meeting title')
        return v.strip()
    @field_validator('attendees')
    @classmethod
    def emails(cls,values):
        values=list(dict.fromkeys(v.strip().lower() for v in values))
        if any(len(v)>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',v) for v in values):raise ValueError('Enter valid attendee email addresses')
        return values
    @model_validator(mode='after')
    def dates_and_link(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:raise ValueError('Meeting times must include a time zone')
        if not timedelta(0)<self.end-self.start<=timedelta(days=7):raise ValueError('End must be after start, within seven days')
        if self.meeting_url:
            url=urlparse(self.meeting_url)
            if url.scheme!='https' or not url.hostname or url.username or url.password:raise ValueError('Use a valid HTTPS meeting link')
        if self.teams and self.meeting_url:raise ValueError('Choose Teams or an existing meeting link')
        return self

def meeting_payload(body):
    def utc(value):return {'dateTime':value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),'timeZone':'UTC'}
    result={'subject':body.subject,'start':utc(body.start),'end':utc(body.end),'transactionId':str(body.transaction_id),
      'body':{'contentType':'text','content':body.description+('\n\nJoin meeting: '+body.meeting_url if body.meeting_url else '')},
      'location':{'displayName':body.meeting_url or body.location},
      'attendees':[{'emailAddress':{'address':email},'type':'required'} for email in body.attendees]}
    if body.teams:result.update(isOnlineMeeting=True,onlineMeetingProvider='teamsForBusiness')
    return result

@router.post('/api/calendar/meetings')
async def create_meeting(request:Request,body:Meeting):
    u=await security.require_user(request,ROLES);await security.enforce_csrf(request,u)
    await security.rate_limit('calendar-write:'+u['user_id'],20,60)
    try:
        token=await calendar_token(u)
        if not token:raise HTTPException(401,'Connect your Microsoft calendar first.')
        raw=await security._redis().get(key(u));stored=json.loads(raw) if raw else {}
        scopes={x.rsplit('/',1)[-1] for x in stored.get('scope','').split()}
        if not scopes&{'Calendars.ReadWrite','Calendars.ReadWrite.Shared'}:raise HTTPException(403,'Enable scheduling to grant Microsoft calendar write access.')
        base=calendar_base(body.calendar_id);headers={'Authorization':'Bearer '+token}
        async with httpx.AsyncClient(timeout=25) as client:
            cal=await graph_result(await client.get(base,headers=headers))
            if not cal.get('canEdit'):raise HTTPException(403,'You do not have permission to schedule on this calendar.')
            if body.teams and 'teamsForBusiness' not in cal.get('allowedOnlineMeetingProviders',[]):raise HTTPException(422,'Teams meetings are not enabled for this calendar. Use an existing meeting link or an in-person meeting.')
            event=await graph_result(await client.post(base+'/events',headers=headers,json=meeting_payload(body)))
        return {'id':event['id'],'subject':event.get('subject'),'webLink':event.get('webLink'),'onlineMeeting':event.get('onlineMeeting')}
    except httpx.HTTPError:raise HTTPException(502,'Microsoft did not confirm the save. Check Outlook or retry the same meeting; the retry uses the same request identifier.')
