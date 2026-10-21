from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app import crm, main

TENANT='11111111-1111-4111-8111-111111111111'
IDENTITY='22222222-2222-4222-8222-222222222222'
USER={'tenant_id':TENANT,'user_id':IDENTITY,'role':'banker'}

@pytest.fixture
def harness(monkeypatch):
    connection=AsyncMock()
    connection.fetchval.return_value=None
    connection.fetchrow.return_value={'id':UUID(IDENTITY)}
    connection.fetch.return_value=[]
    @asynccontextmanager
    async def transaction(user):
        assert user==USER
        yield connection
    auth=AsyncMock(return_value=USER)
    csrf=AsyncMock()
    monkeypatch.setattr(crm,'require_user',auth)
    monkeypatch.setattr(crm,'enforce_csrf',csrf)
    monkeypatch.setattr(crm,'tenant_tx',transaction)
    return TestClient(main.app),connection,auth,csrf

@pytest.mark.parametrize('payload',[{'name':'  '},{'name':'Firm','tenant_id':TENANT},{'name':'Firm','website_url':'javascript:alert(1)'},{'name':'Firm','linkedin_url':'https://evil.invalid/profile'}])
def test_invalid_investor_payloads(harness,payload):
    client,c,_,_=harness
    assert client.post('/api/investors',json=payload).status_code==422
    c.execute.assert_not_awaited()

def test_create_is_internal_csrf_protected_and_audited(harness):
    client,c,auth,csrf=harness
    response=client.post('/api/investors',json={'name':'Test Fund','website_url':'https://example.org/'})
    assert response.status_code==201
    assert auth.await_args.args[1]==crm.WRITE
    csrf.assert_awaited_once()
    assert any('INVESTOR_CREATED' in call.args for call in c.execute.await_args_list)
    assert c.fetchrow.await_args.args[1]==TENANT

def test_duplicate_is_not_silently_merged(harness):
    client,c,_,_=harness
    c.fetchval.return_value=UUID(IDENTITY)
    response=client.post('/api/investors',json={'name':'Existing'})
    assert response.status_code==409
    c.fetchrow.assert_not_awaited()

def test_missing_investor_note_does_not_insert(harness):
    client,c,_,_=harness
    assert client.post(f'/api/investors/{IDENTITY}/notes',json={'summary':'Follow up'}).status_code==404
    c.fetchrow.assert_not_awaited()

def test_prospect_owner_must_be_visible_team_member(harness):
    client,c,_,_=harness
    response=client.post('/api/prospects',json={'name':'Company','owner_user_id':IDENTITY})
    assert response.status_code==422
    c.fetchrow.assert_not_awaited()

def test_task_cannot_reference_missing_or_other_tenant_record(harness):
    client,c,_,_=harness
    response=client.post('/api/tasks',json={'title':'Follow up','organization_id':IDENTITY})
    assert response.status_code==422
    c.fetchrow.assert_not_awaited()

def test_task_cannot_set_arbitrary_status(harness):
    client,c,_,_=harness
    assert client.patch(f'/api/tasks/{IDENTITY}',json={'status':'deleted'}).status_code==422
    c.fetchrow.assert_not_awaited()

@pytest.mark.parametrize('path',['/api/stats','/api/team','/api/prospects','/api/tasks',f'/api/investors/{IDENTITY}',f'/api/deals/{IDENTITY}/investors','/api/audit'])
def test_internal_routes_reject_external_user(harness,path):
    client,c,auth,_=harness
    async def external(request,roles):
        assert 'investor' not in roles
        raise HTTPException(403)
    auth.side_effect=external
    assert client.get(path).status_code==403
    c.fetch.assert_not_awaited()

def test_prospect_role_and_intent_defaults():
    record=crm.Prospect(name='HVAC company')
    assert record.service_need=='unknown'
    assert record.status=='unqualified'
    with pytest.raises(ValidationError): crm.Prospect(name='Company',website_url='data:text/html,test')

def test_contact_names_phone_and_addresses_are_separate():
    contact=crm.Contact(first_name=' Jane ',last_name=' Smith ',email='JANE@example.org',phone='+44 20 1234 5678',street_address='1 Example Street',city='London',postal_code='SW1A 1AA',country='United Kingdom')
    assert contact.name=='Jane Smith'
    assert contact.first_name=='Jane' and contact.last_name=='Smith'
    assert contact.email=='jane@example.org'
    assert contact.phone=='+44 20 1234 5678'
    assert contact.postal_code=='SW1A 1AA'

@pytest.mark.parametrize('payload',[{'email':'not-an-email'},{'linkedin_url':'https://example.org/user'},{'email':'injected\n@example.org'}])
def test_invalid_contact_details_rejected(payload):
    with pytest.raises(ValidationError): crm.Contact(**payload)

def test_published_mandate_requires_source(harness):
    client,c,_,_=harness
    response=client.post(f'/api/investors/{IDENTITY}/mandates',json={'criteria':{'check_min':10,'check_max':5},'evidence_status':'source_stated'})
    assert response.status_code==422
    c.execute.assert_not_awaited()


def test_referral_cannot_spoof_source(harness):
    client,c,_,_=harness
    assert client.post('/api/referral-sources',json={'firm_name':'Test','sourced_by':IDENTITY}).status_code==422
    c.fetchrow.assert_not_awaited()

def test_referral_rejects_cross_tenant_owner(harness):
    client,c,_,_=harness
    assert client.post('/api/referral-sources',json={'firm_name':'Test','owner_user_id':IDENTITY}).status_code==422
    c.fetchrow.assert_not_awaited()

def test_referral_creation_is_audited(harness):
    client,c,auth,csrf=harness
    assert client.post('/api/referral-sources',json={'firm_name':'Test','notes':'Follow up'}).status_code==201
    csrf.assert_awaited_once()
    assert c.fetchrow.await_args.args[1:3]==(TENANT,IDENTITY)
    assert any('REFERRAL_SOURCE_CREATED' in call.args for call in c.execute.await_args_list)

def test_referral_update_preserves_source(harness):
    client,c,_,_=harness
    assert client.patch(f'/api/referral-sources/{IDENTITY}',json={'notes':'Updated'}).status_code==200
    sql=c.fetchrow.await_args.args[0]
    assert 'sourced_by' not in sql and 'owner_user_id' not in sql

def test_saved_search_is_scoped_to_current_user(harness):
    client,c,_,_=harness
    assert client.get('/api/saved-investor-searches').status_code==200
    assert 'created_by=$1' in c.fetch.await_args.args[0]
    assert c.fetch.await_args.args[1]==IDENTITY

def test_saved_search_creation_uses_authenticated_identity(harness):
    client,c,auth,csrf=harness
    assert client.post('/api/saved-investor-searches',json={'name':'Healthcare','filters':{'selections':{'sector':['Healthcare']}}}).status_code==201
    csrf.assert_awaited_once()
    assert c.fetchrow.await_args.args[1:3]==(TENANT,IDENTITY)

def test_investor_criteria_are_saved_with_record(harness):
    client,c,_,_=harness
    r=client.post('/api/investors',json={'name':'Test firm','investment_criteria':{'sectors':['Healthcare'],'currency':'USD','check_max':500000}})
    assert r.status_code==201
    calls=[x for x in c.execute.await_args_list if 'insert into investor_mandates' in x.args[0]]
    assert len(calls)==1
    assert 'unverified' in calls[0].args[0]


def test_invalid_investment_range_does_not_write(harness):
    client,c,_,_=harness
    assert client.post('/api/investors',json={'name':'Test','investment_criteria':{'check_min':10,'check_max':5}}).status_code==422
    c.execute.assert_not_awaited()


def test_missing_deal_history_returns_404(harness):
    client,c,_,_=harness
    assert client.get(f'/api/deals/{IDENTITY}/outreach-history').status_code==404
    c.fetch.assert_not_awaited()


def test_outreach_history_uses_internal_role_and_tenant(harness):
    client,c,auth,_=harness
    c.fetchval.return_value=UUID(IDENTITY)
    response=client.get(f'/api/deals/{IDENTITY}/outreach-history')
    assert response.status_code==200
    assert auth.await_args.args[1]==crm.READ
    assert 'investor' not in crm.READ
    for call in c.fetch.await_args_list:
        assert call.args[1]==TENANT
        assert call.args[2]==IDENTITY
