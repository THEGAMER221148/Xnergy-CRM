import pytest,httpx
from app import library

@pytest.mark.asyncio
async def test_document_link_requires_access_and_xnergy_host(monkeypatch):
    payload={'id':'i','file':{'mimeType':'text/plain'},'webUrl':'https://evil.example/file'}
    class Client:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def get(self,*a,**k):return httpx.Response(200,json=payload)
    monkeypatch.setattr(library.httpx,'AsyncClient',Client)
    assert await library.document_item('token','drive','item') is None
    payload['webUrl']='https://netorg14312891.sharepoint.com/file'
    assert (await library.document_item('token','drive','item'))['id']=='i'

@pytest.mark.asyncio
async def test_current_requires_manager_and_csrf(monkeypatch):
    from fastapi import HTTPException
    seen=[]
    async def auth(request,roles):seen.extend(roles);return {'role':'admin'}
    async def csrf(*a):raise HTTPException(403,'CSRF')
    monkeypatch.setattr(library.security,'require_user',auth);monkeypatch.setattr(library.security,'enforce_csrf',csrf)
    with pytest.raises(HTTPException) as error:await library.mark_current(None,library.CurrentDocument(purpose='NDA',drive_id='d',item_id='i'))
    assert error.value.status_code==403 and seen==['admin','deal_manager','compliance']
