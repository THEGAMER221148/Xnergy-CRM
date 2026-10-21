"""Relationship responsibility is independent of application access roles."""
import json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .db import tenant_tx
from .security import require_user,enforce_csrf
router=APIRouter(prefix='/api/relationship-team')
Kind=Literal['prospect','investor','deal','contact']
class TeamAssignment(BaseModel):
 model_config=ConfigDict(extra='forbid')
 banker_ids:list[UUID]=Field(default_factory=list,max_length=30)
 manager_user_id:UUID|None=None
async def resolve(c,kind,identity):
 table={'prospect':'company_prospects','investor':'investor_organizations','deal':'deals','contact':'prospect_contacts'}[kind]
 r=await c.fetchrow(f'select * from {table} where id=$1',identity)
 if not r:raise HTTPException(404,'Record not found')
 if kind=='deal' and r['prospect_id']:return await resolve(c,'prospect',r['prospect_id'])
 primary=r.get('owner_user_id')
 if kind=='investor':primary=await c.fetchval('select id from users where active and display_name=$1',r['relationship_owner']) if r['relationship_owner'] else None
 return kind,identity,primary,r
async def read_team(c,kind,identity):
 kind,identity,primary,r=await resolve(c,kind,identity)
 a=await c.fetchrow('select * from crm_relationship_teams where entity_kind=$1 and entity_id=$2',kind,identity)
 if kind=='contact' and not a:
  inherited=await read_team(c,'prospect',r['prospect_id']);return {**inherited,'inherited':True}
 bankers=list(a['banker_ids']) if a else []
 if primary and primary not in bankers:bankers.insert(0,primary)
 return {'banker_ids':bankers,'manager_user_id':a['manager_user_id'] if a else None,'primary_banker_id':primary,'legacy_banker':r.get('relationship_owner') if kind=='investor' else None}
async def write_team(c,u,kind,identity,body):
 kind,identity,primary,r=await resolve(c,kind,identity)
 await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':team:'+kind+':'+str(identity))
 ids=list(dict.fromkeys(body.banker_ids))
 for uid in ids+([body.manager_user_id] if body.manager_user_id else []):
  if not await c.fetchval("select id from users where id=$1 and active and role in ('admin','banker','deal_manager')",uid):raise HTTPException(422,'Choose an active team member from this workspace')
 for uid in ids:
  if not await c.fetchval('select id from users where id=$1 and relationship_banker',uid):raise HTTPException(422,'This team member is not a relationship banker')
 before=await read_team(c,kind,identity)
 await c.execute('insert into crm_relationship_teams(tenant_id,entity_kind,entity_id,banker_ids,manager_user_id) values($1::uuid,$2,$3,$4,$5) on conflict(tenant_id,entity_kind,entity_id) do update set banker_ids=excluded.banker_ids,manager_user_id=excluded.manager_user_id,updated_at=now()',u['tenant_id'],kind,identity,ids,body.manager_user_id)
 primary=primary if primary in ids else next(iter(ids),None)
 if kind=='investor':
  name=await c.fetchval('select display_name from users where id=$1',primary) if primary else (r['relationship_owner'] if not before['banker_ids'] else None)
  await c.execute('update investor_organizations set relationship_owner=$2,updated_at=now() where id=$1',identity,name)
 elif kind!='contact':
  table='company_prospects' if kind=='prospect' else 'deals'
  await c.execute(f'update {table} set owner_user_id=$2,updated_at=now() where id=$1',identity,primary)
  if kind=='prospect':await c.execute('update deals set owner_user_id=$2,updated_at=now() where prospect_id=$1',identity,primary)
 await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'RELATIONSHIP_TEAM_UPDATED',kind,str(identity),json.dumps({'before':before,'after':body.model_dump(mode='json')},default=str))
 return await read_team(c,kind,identity)
@router.get('/{kind}/{identity}')
async def get_team(kind:Kind,identity:UUID,request:Request):
 u=await require_user(request,['admin','banker','deal_manager','compliance'])
 async with tenant_tx(u) as c:return await read_team(c,kind,identity)
@router.put('/{kind}/{identity}')
async def save_team(kind:Kind,identity:UUID,body:TeamAssignment,request:Request):
 u=await require_user(request,['admin','banker','deal_manager']);await enforce_csrf(request,u)
 async with tenant_tx(u) as c:return await write_team(c,u,kind,identity,body)
