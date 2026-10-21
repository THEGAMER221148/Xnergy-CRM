from contextlib import asynccontextmanager
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import pipeline

@pytest.mark.asyncio
async def test_activity_checks_identity_and_preserves_stage(monkeypatch):
    uid,tid,did=uuid4(),uuid4(),uuid4();writes=[]
    class C:
        async def fetchrow(self,sql,*args):
            if sql.startswith('select'):return {'tenant_id':tid,'stage':'vetting'}
            writes.append(args);return {'id':uuid4()}
    @asynccontextmanager
    async def tx(u):yield C()
    async def auth(*args):return {'user_id':str(uid),'tenant_id':str(tid)}
    async def csrf(*args):pass
    monkeypatch.setattr(pipeline,'tenant_tx',tx);monkeypatch.setattr(pipeline,'require_user',auth);monkeypatch.setattr(pipeline,'enforce_csrf',csrf)
    await pipeline.add_activity(did,pipeline.Activity(kind='call',summary='Spoke to CFO'),None)
    assert writes[0]==(tid,did,'vetting','Call: Spoke to CFO',str(uid))
    with pytest.raises(HTTPException):await pipeline.add_activity(did,pipeline.Activity(summary='   '),None)

@pytest.mark.asyncio
async def test_same_stage_note_is_saved(monkeypatch):
    tid,did=uuid4(),uuid4();writes=[]
    class C:
        async def fetchrow(self,*args):return {'tenant_id':tid,'stage':'vetting'}
        async def execute(self,sql,*args):writes.append((sql,args))
    @asynccontextmanager
    async def tx(u):yield C()
    async def auth(*args):return {'user_id':str(uuid4())}
    async def csrf(*args):pass
    monkeypatch.setattr(pipeline,'tenant_tx',tx);monkeypatch.setattr(pipeline,'require_user',auth);monkeypatch.setattr(pipeline,'enforce_csrf',csrf)
    await pipeline.change(did,pipeline.Change(stage='vetting',note='Waiting for financials'),None)
    assert len(writes)==1 and writes[0][0].startswith('insert into deal_stage_history')
    assert writes[0][1][3]=='Waiting for financials'
