"""Retained inactive company relationships and explicitly recorded revisit criteria."""
import json
from uuid import UUID
from datetime import date
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .db import tenant_tx
from .security import require_user,enforce_csrf
router=APIRouter()
READ=['admin','banker','deal_manager','compliance'];WRITE=['admin','banker','deal_manager']
class Revisit(BaseModel):
 model_config=ConfigDict(extra='forbid')
 reason:str=Field(default='',max_length=2000)
 follow_up_on:date|None=None
 last_contact_on:date|None=None
 revenue_target:float|None=Field(default=None,ge=0,allow_inf_nan=False)
 currency:Literal['USD','CAD','EUR','GBP','AUD']='USD'
 priority:Literal['normal','high','low']='normal'
 revisit_when:str=Field(default='',max_length=4000)

def object_value(value):return json.loads(value) if isinstance(value,str) else (value or {})
async def list_records(c):
 rows=await c.fetch("""select d.id,d.name,d.prospect_id,d.owner_user_id,d.created_at,
  d.funding_profile,p.sector,p.company_profile,p.geography,p.created_at prospect_added_at,
  coalesce(rt.banker_ids,array[]::uuid[]) banker_ids,
  (select max(h.created_at) from (select stage,created_at,lag(stage) over(order by created_at,id) previous_stage from deal_stage_history where deal_id=d.id) h where h.stage='archived_historical' and h.previous_stage is distinct from 'archived_historical') inactive_since,
  (select max(h.created_at) from deal_stage_history h where h.deal_id=d.id and h.note ~* '^(Call:|Email:|Meeting:|Email accepted)') logged_last_contact,
  (select min(t.due_date) from crm_tasks t where t.deal_id=d.id and t.status='open' and t.task_kind='manual') task_follow_up,
  coalesce((select jsonb_agg(jsonb_build_object('name',pc.name,'email',pc.email,'phone',pc.phone)) from prospect_contacts pc where pc.prospect_id=p.id),'[]'::jsonb) contacts
  from deals d left join company_prospects p on p.id=d.prospect_id
  left join crm_relationship_teams rt on rt.entity_kind='prospect' and rt.entity_id=p.id
  where d.stage='archived_historical' order by d.created_at desc,d.id""")
 result=[]
 for row in rows:
  r=dict(row);fund=object_value(r.pop('funding_profile'));profile=object_value(r.pop('company_profile'));rev=fund.get('inactive_review') or {}
  r.update(review=rev,sector=r['sector'] or fund.get('sector'),annual_revenue=profile.get('annual_revenue'),revenue_currency=profile.get('currency'),contacts=object_value(r['contacts']),added_at=r['prospect_added_at'] or r['created_at'])
  for k in ['added_at','inactive_since']:
   if r[k]:r[k]=r[k].astimezone(ZoneInfo('America/Los_Angeles')).date()
  r['last_contact_on']=max([x for x in [str(r['logged_last_contact'].astimezone(ZoneInfo('America/Los_Angeles')).date()) if r['logged_last_contact'] else None,rev.get('last_contact_on')] if x],default=None)
  r['follow_up_on']=min([x for x in [str(r['task_follow_up']) if r['task_follow_up'] else None,rev.get('follow_up_on')] if x],default=None)
  result.append(r)
 return result
@router.get('/inactive')
async def records(request:Request):
 u=await require_user(request,READ)
 async with tenant_tx(u) as c:return await list_records(c)
async def save_review(c,u,identity,body):
 d=await c.fetchrow('select * from deals where id=$1 for update',identity)
 if not d:raise HTTPException(404,'Lead not found')
 if d['stage']!='archived_historical':raise HTTPException(409,'This lead is active. Refresh the inactive list.')
 profile=object_value(d['funding_profile']);old=profile.get('inactive_review');value=body.model_dump(mode='json')
 if old==value:return {'saved':True}
 profile['inactive_review']=value
 await c.execute('update deals set funding_profile=$2::jsonb,updated_at=now() where id=$1',identity,json.dumps(profile))
 await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'INACTIVE_REVIEW_UPDATED','deal',str(identity),json.dumps({'before':old,'after':value}))
 return {'saved':True}
@router.patch('/inactive/{identity}/review')
async def review(identity:UUID,body:Revisit,request:Request):
 u=await require_user(request,WRITE);await enforce_csrf(request,u)
 async with tenant_tx(u) as c:return await save_review(c,u,identity,body)
@router.post('/inactive/{identity}/restore')
async def restore(identity:UUID,request:Request):
 u=await require_user(request,WRITE);await enforce_csrf(request,u)
 async with tenant_tx(u) as c:
  d=await c.fetchrow('select * from deals where id=$1 for update',identity)
  if not d:raise HTTPException(404,'Lead not found')
  if d['stage']!='archived_historical':return {'id':identity,'stage':d['stage']}
  stage=await c.fetchval("select stage from deal_stage_history where deal_id=$1 and stage in ('initial_identification','contact_made','vetting','engagement_sent','engaged_client') order by created_at desc,id desc limit 1",identity) or 'initial_identification'
  await c.execute('update deals set stage=$2,updated_at=now() where id=$1',identity,stage)
  if d['prospect_id']:
   await c.execute('update company_prospects set status=$2,updated_at=now() where id=$1',d['prospect_id'],{'initial_identification':'unqualified','contact_made':'contacted','vetting':'qualified','engagement_sent':'qualified','engaged_client':'client'}[stage])
  await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',d['tenant_id'],identity,stage,'Restored from Inactive; contacts, documents and revisit criteria retained',u['user_id'])
  return {'id':identity,'stage':stage}
