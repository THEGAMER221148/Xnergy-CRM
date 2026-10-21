import json
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app import library,security

def test_graph_paths_escape_identifiers_and_search():
    path,params=library.browse_path('site','drive/../../evil','folder')
    assert 'drive%2F..%2F..%2Fevil' in path
    path,params=library.browse_path('site','drive',q="O'Brien & WSP")
    assert "O%27%27Brien%20%26%20WSP" in path

@pytest.mark.asyncio
async def test_library_consent_does_not_overwrite_calendar(monkeypatch):
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    user={'tenant_id':'tenant','user_id':'one'}
    await library.save_library_connection(user,{'tenant_id':'tenant','id':'one'},{'user':user,'tokens':{'access_token':'private','scope':'Sites.Read.All'}})
    assert redis.setex.call_args.args[0]=='library:tenant:one'

@pytest.mark.asyncio
async def test_library_rejects_other_account(monkeypatch):
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    user={'tenant_id':'tenant','user_id':'one'}
    with pytest.raises(HTTPException):await library.save_library_connection(user,{'tenant_id':'tenant','id':'two'},{'user':user,'tokens':{'access_token':'private','scope':'Sites.Read.All'}})
    redis.setex.assert_not_awaited()

@pytest.mark.asyncio
async def test_library_listing_excludes_download_tokens(monkeypatch):
    import httpx
    redis=AsyncMock();monkeypatch.setattr(security,'_redis',lambda:redis)
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'one','tenant_id':'tenant'}))
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(library,'token',AsyncMock(return_value='token'))
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kw):return httpx.Response(200,json={'value':[{'id':'doc','name':'WSP.docx','@microsoft.graph.downloadUrl':'SECRET'}]},request=httpx.Request('GET',url))
    monkeypatch.setattr(library.httpx,'AsyncClient',Client)
    data=await library.browse(None,'site','drive','','','')
    assert data['items']==[{'id':'doc','name':'WSP.docx'}]

@pytest.mark.asyncio
async def test_pagination_is_bound_to_user_and_tenant(monkeypatch):
    redis=AsyncMock();redis.get.return_value=None
    monkeypatch.setattr(security,'_redis',lambda:redis)
    monkeypatch.setattr(security,'require_user',AsyncMock(return_value={'user_id':'other','tenant_id':'tenant2'}))
    monkeypatch.setattr(security,'rate_limit',AsyncMock())
    monkeypatch.setattr(library,'token',AsyncMock(return_value='token'))
    with pytest.raises(HTTPException) as e:await library.browse(None,'','','','','stolen-cursor')
    assert e.value.status_code==410
    redis.get.assert_awaited_once_with('library:tenant2:other:page:stolen-cursor')
