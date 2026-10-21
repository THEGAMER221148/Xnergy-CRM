from unittest.mock import AsyncMock
import pytest,httpx
from app import ask,document_search as ds,library

@pytest.mark.asyncio
@pytest.mark.parametrize('text,category',[('Find our NDA','nda'),('Pull up phone scripts','phone'),('Find the engagement letter','engagement'),('Show email templates','email'),('Where are the OBA forms?','oba'),('WSPs','wsp')])
async def test_document_intents(text,category):
    assert await ask.document_plan(text)==ds.CATEGORIES[category]['terms']

@pytest.mark.asyncio
@pytest.mark.parametrize('text',['What is an NDA?','When do I send an engagement agreement?','How do I get approval for OBA?'])
async def test_policy_questions_stay_guidance(text):
    assert await ask.document_plan(text) is None

@pytest.mark.asyncio
async def test_explicit_guidance_mode():
    assert await ask.document_plan('Find our NDA','guidance') is None

def test_query_does_not_accept_kql_operators():
    query=ds.query_string(['NDA" OR path:https://evil.test ('])
    assert 'path:https://' not in query
    assert 'AND path:"https://netorg14312891.sharepoint.com"' in query

def test_cards_only_original_sharepoint_links_without_html_or_duplicates():
    good={'resource':{'id':'one','name':'NDA.docx','webUrl':'https://netorg14312891.sharepoint.com/nda','lastModifiedDateTime':'2026-09-18T00:00:00Z'},'summary':'<c0>Template</c0> &amp; form'}
    bad={'resource':{'name':'Bad','webUrl':'https://evil.example/doc'}}
    cards,more=ds.document_cards({'value':[{'hitsContainers':[{'hits':[good,good,bad],'moreResultsAvailable':True}]}]})
    assert len(cards)==1 and cards[0]['description']=='Template & form'
    assert more and 'approved' not in cards[0]

@pytest.mark.asyncio
async def test_disconnected_does_not_invent_documents(monkeypatch):
    monkeypatch.setattr(library,'token',AsyncMock(return_value=None))
    data=await ds.search_documents({'user_id':'one'},['NDA'])
    assert data['connected'] is False and data['documents']==[]

@pytest.mark.asyncio
async def test_search_uses_calling_users_token_and_pages(monkeypatch):
    gettoken=AsyncMock(return_value='USER_ONLY');monkeypatch.setattr(library,'token',gettoken);calls=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kw):
            calls.append((url,kw));return httpx.Response(200,json={'value':[{'hitsContainers':[{'hits':[],'moreResultsAvailable':False}]}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(ds.httpx,'AsyncClient',Client)
    user={'user_id':'one','tenant_id':'t'}
    await ds.search_documents(user,['NDA'],20)
    gettoken.assert_awaited_once_with(user)
    assert calls[0][1]['headers']['Authorization']=='Bearer USER_ONLY'
    assert calls[0][1]['json']['requests'][0]['from']==20
    assert calls[0][1]['json']['requests'][0]['entityTypes']==['driveItem']

@pytest.mark.asyncio
async def test_ask_returns_document_cards_without_faq_dump(monkeypatch):
    monkeypatch.setattr(ask,'require_user',AsyncMock(return_value={'user_id':'one','tenant_id':'t'}))
    monkeypatch.setattr(ask,'enforce_csrf',AsyncMock());monkeypatch.setattr(ask,'rate_limit',AsyncMock())
    monkeypatch.setattr(ds,'search_documents',AsyncMock(return_value={'connected':True,'documents':[{'name':'NDA.docx','url':'https://netorg14312891.sharepoint.com/nda'}],'has_more':False}))
    faq=AsyncMock();monkeypatch.setattr(ask,'answer_question',faq)
    result=await ask.ask(ask.Question(question='Find our NDA'),None)
    assert result['kind']=='documents' and result['source_pages']==[]
    faq.assert_not_awaited()
