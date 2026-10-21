"""Shared follow-up workspace for Today and Team follow-ups."""
from uuid import UUID
from datetime import date
from typing import Literal
from fastapi import APIRouter,Request,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from .db import tenant_tx
from .security import require_user,enforce_csrf
from .pipeline import ensure_deal
router=APIRouter(prefix='/api/tasks')
READ=['admin','banker','deal_manager','compliance'];WRITE=['admin','banker','deal_manager']
class Edit(BaseModel):
 model_config=ConfigDict(extra='forbid')
 title:str|None=Field(default=None,min_length=1,max_length=500)
 assigned_to:UUID|None=None
 due_date:date|None=None
 status:Literal['open','done','cancelled']|None=None
class Update(BaseModel):
 summary:str=Field(min_length=1,max_length=4000)

@router.get('/{identity}/workspace')
async def workspace(identity:UUID,request:Request):
 u=await require_user(request,READ)
 async with tenant_tx(u) as c:
  t=await c.fetchrow('select * from crm_tasks where id=$1',identity)
  if not t:raise HTTPException(404,'Follow-up not found.')
  t=dict(t);company=None;contacts=[];activity=[]
  if t['organization_id']:
   company=await c.fetchrow('select id,name from investor_organizations where id=$1',t['organization_id'])
   contacts=await c.fetch('select * from investor_contacts where organization_id=$1 order by name',t['organization_id'])
   activity=await c.fetch('select summary note,created_at from interactions where organization_id=$1 order by created_at desc limit 30',t['organization_id'])
  else:
   d=await c.fetchrow('select * from deals where id=$1',t['deal_id']) if t['deal_id'] else await c.fetchrow('select * from deals where prospect_id=$1',t['prospect_id']) if t['prospect_id'] else None
   if d:t['deal_id']=d['id'];t['prospect_id']=t['prospect_id'] or d['prospect_id'];company={'name':d['name']}
   if t['prospect_id']:
    company=await c.fetchrow('select id,name from company_prospects where id=$1',t['prospect_id'])
    contacts=await c.fetch('select * from prospect_contacts where prospect_id=$1 order by is_primary desc,name',t['prospect_id'])
   if d:activity=await c.fetch('select note,created_at from deal_stage_history where deal_id=$1 order by created_at desc limit 30',d['id'])
 return {'task':t,'company':dict(company) if company else None,'contacts':[dict(x) for x in contacts],'activity':[dict(x) for x in activity]}

@router.put('/{identity}')
async def edit(identity:UUID,body:Edit,request:Request):
 u=await require_user(request,WRITE);await enforce_csrf(request,u)
 values=body.model_dump(exclude_unset=True)
 if any(k in values and not values[k] for k in ('title','status')):raise HTTPException(422,'Keep a task title and status.')
 async with tenant_tx(u) as c:
  if not await c.fetchval('select id from crm_tasks where id=$1 for update',identity):raise HTTPException(404,'Follow-up not found.')
  if body.assigned_to and not await c.fetchval("select id from users where id=$1 and active and role in ('admin','banker','deal_manager')",body.assigned_to):raise HTTPException(422,'Select an active banker.')
  for k,v in values.items():await c.execute(f'update crm_tasks set {k}=$2 where id=$1',identity,v)
  await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'FOLLOW_UP_EDITED','crm_task',str(identity),'{}')
 return {'saved':True}

@router.post('/{identity}/updates')
async def update(identity:UUID,body:Update,request:Request):
 u=await require_user(request,WRITE);await enforce_csrf(request,u)
 if not body.summary.strip():raise HTTPException(422,'Enter an update.')
 async with tenant_tx(u) as c:
  t=await c.fetchrow('select * from crm_tasks where id=$1 for update',identity)
  if not t:raise HTTPException(404,'Follow-up not found.')
  note='Follow-up: '+t['title']+'\n'+body.summary.strip()
  if t['organization_id']:
   await c.execute("insert into interactions(tenant_id,organization_id,interaction_type,summary,created_by) values($1::uuid,$2,'note',$3,$4::uuid)",u['tenant_id'],t['organization_id'],note,u['user_id'])
  else:
   d=await c.fetchrow('select * from deals where id=$1',t['deal_id']) if t['deal_id'] else await ensure_deal(c,u,t['prospect_id']) if t['prospect_id'] else None
   if not d:raise HTTPException(422,'This task has no linked company or investor. Open a CRM profile to add a relationship follow-up.')
   await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1::uuid,$2,$3,$4,$5::uuid)',u['tenant_id'],d['id'],d['stage'],note,u['user_id'])
  await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'FOLLOW_UP_UPDATED','crm_task',str(identity),'{}')
 return {'saved':True}

@router.get('/calendar/list')
async def calendar_list(request:Request,start:date,end:date,offset:int=Query(0,ge=0)):
 u=await require_user(request,READ)
 if start>=end:raise HTTPException(422,'Invalid date range.')
 async with tenant_tx(u) as c:
  rows=await c.fetch("""select t.id,t.title,t.due_date,t.organization_id,coalesce(t.prospect_id,d.prospect_id) prospect_id,t.deal_id,coalesce(o.name,p.name,d.name) company_name
   from crm_tasks t left join investor_organizations o on o.id=t.organization_id left join company_prospects p on p.id=t.prospect_id left join deals d on d.id=t.deal_id
   where t.assigned_to=$1::uuid and t.status='open' and t.task_kind='manual' and t.due_date>=$2 and t.due_date<$3
   order by t.due_date,t.created_at,t.id limit 101 offset $4""",u['user_id'],start,end,offset)
 return {'items':[dict(r) for r in rows[:100]],'has_more':len(rows)>100}
