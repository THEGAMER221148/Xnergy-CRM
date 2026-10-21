"""Delegated, per-user SharePoint browsing; document contents remain in SharePoint."""
import json,time,secrets,asyncio
from urllib.parse import quote,urlparse
import httpx
from fastapi import APIRouter,Request,HTTPException,Query
from . import security
from .config import get_settings
from .workspace import ROLES
router=APIRouter()
SCOPES='openid profile email offline_access https://graph.microsoft.com/Sites.Read.All'
def key(u):return f"library:{u['tenant_id']}:{u['user_id']}"

@router.get('/auth/library/connect')
async def connect(request:Request,next:str="/static/workspace/library.html"):
    u=await security.require_user(request,ROLES)
    destination=next if next in ('/static/workspace/ask.html','/static/workspace/library.html') else '/static/workspace/library.html'
    return await security.oidc_login(destination,{'tenant_id':u['tenant_id'],'user_id':u['user_id'],'scopes':SCOPES,'purpose':'library'})

async def save_library_connection(current,row,connection):
    if any(str(current[k])!=str(connection['user'][k]) for k in ('tenant_id','user_id')) or str(row['id'])!=str(current['user_id']) or str(row['tenant_id'])!=str(current['tenant_id']):raise HTTPException(403,'Connect the same Microsoft account you use for DealOS.')
    t=connection['tokens']
    if not t.get('access_token') or 'Sites.Read.All' not in {x.rsplit('/',1)[-1] for x in t.get('scope','').split()}:raise HTTPException(403,'Microsoft SharePoint read permission was not granted.')
    await security._redis().setex(key(current),28*86400,json.dumps({'access_token':t['access_token'],'refresh_token':t.get('refresh_token'),'expires_at':time.time()+int(t.get('expires_in',3600))}))

async def token(u):
    raw=await security._redis().get(key(u))
    if not raw:return None
    t=json.loads(raw)
    if t['expires_at']>time.time()+60:return t['access_token']
    if not t.get('refresh_token'):return None
    s=get_settings();d=await security.discovery()
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post(d['token_endpoint'],data={'grant_type':'refresh_token','refresh_token':t['refresh_token'],'client_id':s.oidc_client_id,'client_secret':s.oidc_client_secret,'scope':SCOPES})
    if r.status_code in (400,401):
        await security._redis().delete(key(u));return None
    r.raise_for_status();new=r.json();t.update(access_token=new['access_token'],refresh_token=new.get('refresh_token',t['refresh_token']),expires_at=time.time()+int(new.get('expires_in',3600)))
    await security._redis().setex(key(u),28*86400,json.dumps(t));return t['access_token']

@router.post('/api/library/disconnect')
async def disconnect(request:Request):
    u=await security.require_user(request,ROLES);await security.enforce_csrf(request,u)
    await security._redis().delete(key(u));return {'ok':True}

def browse_path(site='',drive='',folder='',q=''):
    enc=lambda s:quote(s,safe='')
    if not site:return '/sites',{'search':'*','$select':'id,displayName,name,webUrl','$top':'100'}
    if not drive:return '/sites/'+enc(site)+'/drives',{'$select':'id,name,webUrl,driveType','$top':'100'}
    if q:
        return '/drives/'+enc(drive)+"/root/search(q='"+enc(q.replace("'","''"))+"')",{'$select':'id,name,webUrl,folder,file,size,lastModifiedDateTime,parentReference','$top':'100'}
    return '/drives/'+enc(drive)+('/items/'+enc(folder) if folder else '/root')+'/children',{'$select':'id,name,webUrl,folder,file,size,lastModifiedDateTime,parentReference','$top':'100'}

@router.get('/api/library/browse')
async def browse(request:Request,site:str=Query('',max_length=1024),drive:str=Query('',max_length=1024),folder:str=Query('',max_length=1024),q:str=Query('',max_length=200),cursor:str=Query('',max_length=100)):
    u=await security.require_user(request,ROLES);await security.rate_limit('library-read:'+u['user_id'],60,60)
    try:
        access=await token(u)
        if not access:return {'connected':False,'items':[],'cursor':None}
        if cursor:
            raw=await security._redis().get(key(u)+':page:'+cursor)
            if not raw:raise HTTPException(410,'This page expired. Refresh the folder to continue.')
            url=raw;params=None
        else:
            path,params=browse_path(site,drive,folder,q.strip());url='https://graph.microsoft.com/v1.0'+path
        if urlparse(url).scheme!='https' or urlparse(url).netloc!='graph.microsoft.com':raise HTTPException(502,'Unexpected SharePoint response')
        async with httpx.AsyncClient(timeout=25) as client:
            r=await client.get(url,params=params,headers={'Authorization':'Bearer '+access})
        if r.status_code==401:
            await security._redis().delete(key(u));return {'connected':False,'items':[],'cursor':None}
        if r.status_code==403:raise HTTPException(403,'Microsoft denied access. Reconnect SharePoint or ask your administrator for permission to this site or folder.')
        if r.status_code==404:raise HTTPException(404,'The folder was moved, removed, or is no longer accessible to you.')
        if r.status_code==429:raise HTTPException(429,'SharePoint is busy. Please wait a moment and refresh.')
        r.raise_for_status();data=r.json();next_cursor=None
        if data.get('@odata.nextLink'):
            next_cursor=secrets.token_urlsafe(24);await security._redis().setex(key(u)+':page:'+next_cursor,600,data['@odata.nextLink'])
        # Explicit projection excludes pre-authorized download links and unrelated metadata.
        fields=('id','displayName','name','webUrl','folder','file','size','lastModifiedDateTime','parentReference','driveType')
        return {'connected':True,'items':[{k:item[k] for k in fields if k in item} for item in data.get('value',[])],'cursor':next_cursor}
    except httpx.HTTPError:raise HTTPException(502,'SharePoint is temporarily unavailable. Please refresh.')

@router.get('/api/library/important')
async def important(request:Request,category:str=Query('nda',max_length=40),offset:int=Query(0,ge=0,le=1000)):
    from .document_search import CATEGORIES,search_documents
    u=await security.require_user(request,ROLES);await security.rate_limit('library-search:'+u['user_id'],30,60)
    if category not in CATEGORIES:raise HTTPException(422,'Unknown document category')
    data=await search_documents(u,CATEGORIES[category]['terms'],offset)
    return {**data,'category':category,'title':CATEGORIES[category]['title']}

from pydantic import BaseModel,Field
from .db import tenant_tx
class CurrentDocument(BaseModel):
    purpose:str=Field(min_length=1,max_length=200)
    drive_id:str=Field(min_length=1,max_length=1024)
    item_id:str=Field(min_length=1,max_length=1024)

async def document_item(access,drive,item):
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get('https://graph.microsoft.com/v1.0/drives/'+quote(drive,safe='')+'/items/'+quote(item,safe=''),params={'$select':'id,name,webUrl,file,lastModifiedDateTime'},headers={'Authorization':'Bearer '+access})
    if r.status_code in (403,404):return None
    if r.status_code==401:raise HTTPException(401,'Reconnect SharePoint to continue.')
    if r.status_code>=400:raise HTTPException(502,'SharePoint could not verify this document. Please try again.')
    data=r.json();url=urlparse(data.get('webUrl',''))
    if not data.get('file') or url.scheme!='https' or url.hostname!='netorg14312891.sharepoint.com':return None
    return data

@router.get('/api/library/current')
async def current_documents(request:Request):
    u=await security.require_user(request,ROLES)
    access=await token(u)
    can_manage=u['role'] in ('admin','deal_manager','compliance')
    if not access:return {'connected':False,'documents':[],'can_manage':can_manage}
    async with tenant_tx(u) as c:
        rows=await c.fetch('select l.*,u.display_name marked_by_name from library_current_documents l join users u on u.id=l.marked_by order by purpose limit 100')
    gate=asyncio.Semaphore(5)
    async def accessible(row):
        async with gate:item=await document_item(access,row['drive_id'],row['item_id'])
        return {**item,'purpose':row['purpose'],'marked_by':row['marked_by_name'],'marked_at':row['marked_at']} if item else None
    documents=[d for d in await asyncio.gather(*(accessible(row) for row in rows)) if d]
    return {'connected':True,'documents':documents,'can_manage':can_manage}

@router.post('/api/library/current')
async def mark_current(request:Request,body:CurrentDocument):
    u=await security.require_user(request,['admin','deal_manager','compliance']);await security.enforce_csrf(request,u)
    purpose=' '.join(body.purpose.split())
    if not purpose:raise HTTPException(422,'Enter a document purpose')
    access=await token(u)
    if not access:raise HTTPException(409,'Connect SharePoint first')
    item=await document_item(access,body.drive_id,body.item_id)
    if not item:raise HTTPException(404,'Document unavailable in your Xnergy SharePoint access')
    async with tenant_tx(u) as c:
        old=await c.fetchrow('select drive_id,item_id from library_current_documents where purpose=$1',purpose.casefold())
        row=await c.fetchrow('insert into library_current_documents(tenant_id,purpose,drive_id,item_id,marked_by) values($1::uuid,$2,$3,$4,$5::uuid) on conflict(tenant_id,purpose) do update set drive_id=excluded.drive_id,item_id=excluded.item_id,marked_by=excluded.marked_by,marked_at=now() returning id',u['tenant_id'],purpose.casefold(),body.drive_id,body.item_id,u['user_id'])
        await c.execute("select audit_event($1,'LIBRARY_CURRENT_SET','library_document',$2,$3::jsonb)",u['user_id'],str(row['id']),json.dumps({'purpose':purpose,'previous':dict(old) if old else None,'drive_id':body.drive_id,'item_id':body.item_id}))
    return {'ok':True}
