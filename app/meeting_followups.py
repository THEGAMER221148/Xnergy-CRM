"""Shared CRM follow-ups keyed by a meeting's cross-mailbox calendar UID."""
import hashlib
from datetime import date
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel, Field
from .db import tenant_tx
from .security import require_user, enforce_csrf
from .pipeline import ensure_deal
router=APIRouter(prefix='/api/meeting-followups')
READ=['admin','banker','deal_manager','compliance']
WRITE=['admin','banker','deal_manager']
class Followup(BaseModel):
    request_id: UUID
    meeting_uid: str=Field(min_length=1,max_length=2048)
    subject: str=Field(min_length=1,max_length=500)
    record_type: Literal['prospect','investor']
    record_id: UUID
    note: str=Field(default='',max_length=4000)
    next_step: str=Field(default='',max_length=500)
    assigned_to: UUID|None=None
    due_date: date|None=None

def key(uid):return hashlib.sha256(uid.encode()).hexdigest()

@router.get('/records')
async def records(request:Request,q:str=Query('',min_length=2,max_length=200),kind:Literal['prospect','investor']='prospect'):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        table='company_prospects' if kind=='prospect' else 'investor_organizations'
        return [dict(r) for r in await c.fetch(f"select id,name from {table} where name ilike '%'||$1||'%' order by name limit 30",q)]

@router.get('')
async def entries(request:Request,meeting_uid:str=Query(...,min_length=1,max_length=2048)):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        return [dict(r) for r in await c.fetch('''select m.id,m.subject,m.record_type,m.record_id,m.note,m.created_at,m.task_id,
         coalesce(p.name,o.name) record_name,t.title next_step,t.status,t.due_date,a.display_name assignee,u.display_name author
         from crm_meeting_followups m left join crm_tasks t on t.id=m.task_id
         left join users a on a.id=t.assigned_to left join users u on u.id=m.created_by
         left join company_prospects p on m.record_type='prospect' and p.id=m.record_id
         left join investor_organizations o on m.record_type='investor' and o.id=m.record_id
         where m.meeting_key=$1 order by m.created_at,m.id''',key(meeting_uid))]

async def save_followup(c,u,b):
    # Lock serializes retries for this tenant and request; an uncertain response can safely be retried.
    await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',u['tenant_id']+str(b.request_id))
    old=await c.fetchrow('select id,payload_hash from crm_meeting_followups where id=$1',b.request_id)
    digest=hashlib.sha256(b.model_dump_json().encode()).hexdigest()
    if old:
        if old['payload_hash']!=digest:raise HTTPException(409,'This saved request has changed. Reopen Follow up to add another update.')
        return {'id':old['id']}
    if not b.note.strip() and not b.next_step.strip():raise HTTPException(422,'Add a note or a next step.')
    table='company_prospects' if b.record_type=='prospect' else 'investor_organizations'
    if not await c.fetchval(f'select id from {table} where id=$1',b.record_id):raise HTTPException(422,'Choose an available CRM record.')
    if b.assigned_to and not await c.fetchval("select id from users where id=$1 and active and role in ('admin','banker','deal_manager')",b.assigned_to):raise HTTPException(422,'Choose an active team member.')
    deal=await ensure_deal(c,u,b.record_id) if b.record_type=='prospect' else None
    summary='Meeting: '+b.subject+'\n'+b.note.strip()
    if b.next_step.strip():summary+='\nNext step: '+b.next_step.strip()
    if deal:
        await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1::uuid,$2,$3,$4,$5::uuid)',u['tenant_id'],deal['id'],deal['stage'],summary,u['user_id'])
    else:
        await c.execute("insert into interactions(tenant_id,organization_id,interaction_type,summary,created_by) values($1::uuid,$2,'meeting',$3,$4::uuid)",u['tenant_id'],b.record_id,summary,u['user_id'])
    task=None
    if b.next_step.strip():
        task=await c.fetchval('''insert into crm_tasks(tenant_id,title,assigned_to,due_date,organization_id,prospect_id,deal_id)
          values($1::uuid,$2,$3,$4,$5,$6,$7) returning id''',u['tenant_id'],b.next_step.strip(),b.assigned_to,b.due_date,b.record_id if not deal else None,b.record_id if deal else None,deal['id'] if deal else None)
    await c.execute('''insert into crm_meeting_followups(id,tenant_id,meeting_key,subject,record_type,record_id,note,task_id,created_by,payload_hash)
     values($1,$2::uuid,$3,$4,$5,$6,$7,$8,$9::uuid,$10)''',b.request_id,u['tenant_id'],key(b.meeting_uid),b.subject,b.record_type,b.record_id,b.note.strip(),task,u['user_id'],digest)
    return {'id':b.request_id}

@router.post('')
async def save(body:Followup,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:return await save_followup(c,u,body)
