import json,time
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app import workspace,security

@pytest.mark.asyncio
async def test_calendar_cannot_attach_other_users_account(monkeypatch):
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    u={'tenant_id':'tenant','user_id':'one'}
    with pytest.raises(HTTPException) as e:
        await workspace.save_calendar_connection(u,{'id':'two','tenant_id':'tenant'},{'user':u,'tokens':{'access_token':'secret','scope':'Calendars.ReadBasic'}})
    assert e.value.status_code==403
    redis.setex.assert_not_awaited()

@pytest.mark.asyncio
async def test_calendar_tokens_are_namespaced_and_not_returned(monkeypatch):
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    u={'tenant_id':'tenant','user_id':'one'}
    result=await workspace.save_calendar_connection(u,{'id':'one','tenant_id':'tenant'},{'user':u,'tokens':{'access_token':'secret','scope':'Calendars.ReadBasic','expires_in':3600}})
    assert result is None
    assert redis.setex.call_args.args[0]=='calendar:tenant:one'
    assert json.loads(redis.setex.call_args.args[2])['access_token']=='secret'

@pytest.mark.asyncio
async def test_calendar_no_consent_no_storage(monkeypatch):
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    u={'tenant_id':'tenant','user_id':'one'}
    with pytest.raises(HTTPException):await workspace.save_calendar_connection(u,{'id':'one','tenant_id':'tenant'},{'user':u,'tokens':{'access_token':'secret','scope':'openid'}})
    redis.setex.assert_not_awaited()

@pytest.mark.asyncio
async def test_events_disconnected_is_not_fake_calendar(monkeypatch):
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one'}))
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(workspace,'calendar_token',AsyncMock(return_value=None))
    assert await workspace.events(None,'America/Los_Angeles')=={'connected':False,'events':[]}

@pytest.mark.asyncio
async def test_events_paginates_cancellations_and_private_scope(monkeypatch):
    import httpx
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one'}))
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(workspace,'calendar_token',AsyncMock(return_value='test-token'))
    replies=[{'value':[{'subject':'Actual meeting'},{'subject':'Cancelled','isCancelled':True}],'@odata.nextLink':'https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=next'},{'value':[{'subject':'Second meeting'}]}]
    calls=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kw):
            calls.append((url,kw));return httpx.Response(200,json=replies.pop(0),request=httpx.Request('GET',url))
    monkeypatch.setattr(workspace.httpx,'AsyncClient',Client)
    result=await workspace.events(None,'America/Los_Angeles')
    assert [e['subject'] for e in result['events']]==['Actual meeting','Second meeting']
    assert len(calls)==2 and calls[1][1]['params'] is None
    assert result['has_more'] is False
    assert 'test-token' not in json.dumps(result)

@pytest.mark.asyncio
async def test_revoked_refresh_requires_reconnection(monkeypatch):
    import httpx
    redis=AsyncMock();redis.get.return_value=json.dumps({'access_token':'old','refresh_token':'old-refresh','expires_at':0})
    monkeypatch.setattr(security,'_redis',lambda:redis)
    monkeypatch.setattr(security,'discovery',AsyncMock(return_value={'token_endpoint':'https://login.microsoftonline.com/token'}))
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kw):return httpx.Response(400,json={'error':'invalid_grant'})
    monkeypatch.setattr(workspace.httpx,'AsyncClient',Client)
    assert await workspace.calendar_token({'tenant_id':'tenant','user_id':'one'}) is None
    redis.delete.assert_awaited_once_with('calendar:tenant:one')

@pytest.mark.asyncio
async def test_calendar_range_validation(monkeypatch):
    from datetime import date
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one'}))
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    for start,end in [(date(2026,1,1),None),(date(2026,1,1),date(2026,4,1)),(date(2026,1,1),date(2025,12,1))]:
        with pytest.raises(HTTPException) as e:await workspace.events(None,'America/Los_Angeles',start,end)
        assert e.value.status_code==422

def test_meeting_payload_utc_attendees_and_retry_id():
    from uuid import uuid4
    body=workspace.Meeting(subject='Planning',start='2026-09-18T10:00:00-07:00',end='2026-09-18T11:00:00-07:00',attendees=['Person@example.com','person@example.com'],transaction_id=uuid4(),meeting_url='https://example.zoom.us/j/123')
    payload=workspace.meeting_payload(body)
    assert payload['start']=={'dateTime':'2026-09-18T17:00:00','timeZone':'UTC'}
    assert len(payload['attendees'])==1
    assert payload['transactionId']==str(body.transaction_id)
    assert 'https://example.zoom.us/j/123' in payload['body']['content']
    assert 'isOnlineMeeting' not in payload

@pytest.mark.parametrize('changes',[{'end':'2026-09-18T09:00:00Z'},{'start':'2026-09-18T10:00:00'},{'meeting_url':'javascript:alert(1)'},{'attendees':['not-an-email']},{'teams':True,'meeting_url':'https://example.com/join'}])
def test_reject_invalid_meeting(changes):
    from uuid import uuid4
    from pydantic import ValidationError
    with pytest.raises(ValidationError):workspace.Meeting(**dict({'subject':'Meeting','start':'2026-09-18T10:00:00Z','end':'2026-09-18T11:00:00Z','transaction_id':uuid4()},**changes))

@pytest.mark.asyncio
async def test_readonly_calendar_cannot_schedule(monkeypatch):
    from uuid import uuid4
    redis=AsyncMock();redis.get.return_value=json.dumps({'scope':'Calendars.ReadBasic'})
    monkeypatch.setattr(security,'_redis',lambda:redis)
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one','tenant_id':'tenant'}))
    monkeypatch.setattr(security,'enforce_csrf',AsyncMock())
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(workspace,'calendar_token',AsyncMock(return_value='token'))
    body=workspace.Meeting(subject='Meeting',start='2026-09-18T10:00:00Z',end='2026-09-18T11:00:00Z',transaction_id=uuid4())
    with pytest.raises(HTTPException) as e:await workspace.create_meeting(None,body)
    assert e.value.status_code==403

@pytest.mark.asyncio
async def test_create_meeting_preserves_retry_id_after_permission_checks(monkeypatch):
    import httpx
    from uuid import uuid4
    redis=AsyncMock();redis.get.return_value=json.dumps({'scope':'Calendars.ReadWrite'})
    monkeypatch.setattr(security,'_redis',lambda:redis)
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one','tenant_id':'tenant'}))
    csrf=AsyncMock();monkeypatch.setattr(security,'enforce_csrf',csrf)
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(workspace,'calendar_token',AsyncMock(return_value='token'))
    posts=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kw):return httpx.Response(200,json={'canEdit':True,'allowedOnlineMeetingProviders':['teamsForBusiness']})
        async def post(self,url,**kw):
            posts.append((url,kw['json']));return httpx.Response(201,json={'id':'meeting'})
    monkeypatch.setattr(workspace.httpx,'AsyncClient',Client)
    body=workspace.Meeting(subject='Meeting',start='2026-09-18T10:00:00Z',end='2026-09-18T11:00:00Z',teams=True,transaction_id=uuid4())
    result=await workspace.create_meeting(None,body)
    assert result['id']=='meeting'
    assert posts[0][0]=='https://graph.microsoft.com/v1.0/me/calendar/events'
    assert posts[0][1]['transactionId']==str(body.transaction_id)
    assert posts[0][1]['onlineMeetingProvider']=='teamsForBusiness'
    csrf.assert_awaited_once()
