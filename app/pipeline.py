"""Linked prospect origination pipeline; existing document permissions remain in force."""
import json
from uuid import UUID
from datetime import date
from typing import Literal
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from .db import tenant_tx
from .security import require_user, enforce_csrf
router=APIRouter(prefix='/api')
READ=['admin','banker','deal_manager','compliance']
WRITE=['admin','banker','deal_manager']
Stage=Literal['initial_identification','contact_made','vetting','engagement_sent','engaged_client','completed','archived_historical']
class Link(BaseModel):
    deal_id: UUID | None = None
class Change(BaseModel):
    stage: Stage
    note: str = Field(default='',max_length=4000)
async def ensure_deal(c,u,identity,existing=None):
    p=await c.fetchrow('select * from company_prospects where id=$1 for update',identity)
    if not p: raise HTTPException(404,'Company prospect not found')
    d=await c.fetchrow('select * from deals where prospect_id=$1',identity)
    if d:
        if existing and d['id']!=existing:raise HTTPException(409,'This company already has a linked deal')
        return dict(d)
    if existing:
        d=await c.fetchrow('select * from deals where id=$1 for update',existing)
        if not d:raise HTTPException(404,'Deal not found')
        if d['prospect_id']:raise HTTPException(409,'This deal already belongs to another company')
        d=await c.fetchrow('update deals set prospect_id=$2,updated_at=now() where id=$1 returning *',existing,identity)
    else:
        profile=p['company_profile'];profile=json.loads(profile) if isinstance(profile,str) else dict(profile)
        profile.update(sector=p['sector'],funding_amount=profile.get('capital_sought'))
        st={'contacted':'contact_made','researching':'vetting','qualified':'vetting','client':'engaged_client'}.get(p['status'],'initial_identification')
        d=await c.fetchrow("insert into deals(tenant_id,name,mandate_type,stage,owner_user_id,funding_profile,prospect_id) values($1,$2,$3,$4,$5,$6::jsonb,$7) returning *",p['tenant_id'],p['name'],p['service_need'],st,p['owner_user_id'],json.dumps(profile),identity)
    await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',p['tenant_id'],d['id'],d['stage'],'Company prospect linked to pipeline',u['user_id'])
    return dict(d)
@router.post('/prospects/{identity}/pipeline')
async def link(identity:UUID,body:Link,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:return await ensure_deal(c,u,identity,body.deal_id)
@router.patch('/deals/{identity}/stage')
async def change(identity:UUID,body:Change,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        d=await c.fetchrow('select * from deals where id=$1 for update',identity)
        if not d:raise HTTPException(404,'Deal not found')
        if d['stage']!=body.stage:
            await c.execute('update deals set stage=$2,updated_at=now() where id=$1',identity,body.stage)
            await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',d['tenant_id'],identity,body.stage,body.note,u['user_id'])
            if d['prospect_id'] and body.stage!='archived_historical':
                status={'initial_identification':'unqualified','contact_made':'contacted','vetting':'qualified','engagement_sent':'qualified','engaged_client':'client','completed':'client','archived_historical':'unqualified'}[body.stage]
                await c.execute('update company_prospects set status=$2,updated_at=now() where id=$1',d['prospect_id'],status)
        elif body.note.strip():
            await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',d['tenant_id'],identity,body.stage,body.note.strip(),u['user_id'])
        return {'id':identity,'stage':body.stage}
@router.get('/deals/{identity}/stage-history')
async def history(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from deals where id=$1',identity):raise HTTPException(404,'Deal not found')
        return [dict(x) for x in await c.fetch('select h.stage,h.note,h.created_at,u.display_name banker from deal_stage_history h join users u on u.id=h.changed_by where deal_id=$1 order by h.created_at,h.id',identity)]

class Activity(BaseModel):
    kind: Literal['note','call','email','meeting'] = 'note'
    summary: str = Field(min_length=1,max_length=4000)

@router.post('/deals/{identity}/activity',status_code=201)
async def add_activity(identity:UUID,body:Activity,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    if not body.summary.strip():raise HTTPException(422,'Enter a summary')
    async with tenant_tx(u) as c:
        d=await c.fetchrow('select * from deals where id=$1 for update',identity)
        if not d:raise HTTPException(404,'Deal not found')
        row=await c.fetchrow('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid) returning id',d['tenant_id'],identity,d['stage'],body.kind.title()+': '+body.summary.strip(),u['user_id'])
        return dict(row)

@router.get('/deals/{identity}/workspace')
async def deal_workspace(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        d=await c.fetchrow('select * from deals where id=$1',identity)
        if not d:raise HTTPException(404,'Deal not found')
        contacts=await c.fetch('select * from prospect_contacts where prospect_id=$1 order by is_primary desc,name',d['prospect_id']) if d['prospect_id'] else []
        tasks=await c.fetch('select t.*,u.display_name assignee from crm_tasks t left join users u on u.id=t.assigned_to where t.deal_id=$1 order by (t.status=\'open\') desc,t.due_date nulls last,t.created_at desc limit 100',identity)
        activity=await c.fetch('select h.id,h.stage,h.note,h.created_at,u.display_name banker from deal_stage_history h left join users u on u.id=h.changed_by where deal_id=$1 order by h.created_at desc,h.id desc limit 100',identity)
        return {'prospect_id':d['prospect_id'],'contacts':[dict(x) for x in contacts],'tasks':[dict(x) for x in tasks],'activity':[dict(x) for x in activity]}

from .crm_models import Record
from .crm import Contact, insert_contact
class WorkspaceTask(Record):
    due_date: date | None = None
    id: UUID | None = None
    title: str = Field(min_length=1,max_length=500)
    assigned_to: UUID | None = None
    status: Literal['open','done','cancelled'] = 'open'
class WorkspaceActivity(Record):
    id: UUID
    note: str = Field(max_length=4000)
class WorkspaceContact(Contact):
    notes: str | None = Field(default=None,max_length=10000)
    id: UUID | None = None
class WorkspaceSave(Record):
    mandate_type: Literal['unknown','capital_raise','business_sale','both'] | None = None
    stage: Stage | None = None
    stage_note: str = Field(default='',max_length=4000)
    owner_user_id: UUID | None = None
    tasks: list[WorkspaceTask] = Field(default_factory=list,max_length=100)
    activity_edits: list[WorkspaceActivity] = Field(default_factory=list,max_length=100)
    activity: Activity | None = None
    contacts: list[WorkspaceContact] = Field(default_factory=list,max_length=100)
async def save_workspace_changes(c,u,identity,body):
    d=await c.fetchrow('select * from deals where id=$1 for update',identity)
    if not d:raise HTTPException(404,'Deal not found')
    for uid in [body.owner_user_id]+[t.assigned_to for t in body.tasks]:
        if uid and not await c.fetchval("select id from users where id=$1 and active and role in ('admin','banker','deal_manager')",uid):raise HTTPException(422,'Choose an available team member')
    if body.owner_user_id and not await c.fetchval('select id from users where id=$1 and relationship_banker',body.owner_user_id):raise HTTPException(422,'Choose a relationship banker')
    if 'owner_user_id' in body.model_fields_set:
        await c.execute('update deals set owner_user_id=$2,updated_at=now() where id=$1',identity,body.owner_user_id)
        if d['prospect_id']:await c.execute('update company_prospects set owner_user_id=$2,updated_at=now() where id=$1',d['prospect_id'],body.owner_user_id)
    if 'mandate_type' in body.model_fields_set:
        mandate=body.mandate_type or 'unknown'
        await c.execute('update deals set mandate_type=$2,updated_at=now() where id=$1',identity,mandate)
        if d['prospect_id']:await c.execute('update company_prospects set service_need=$2,updated_at=now() where id=$1',d['prospect_id'],mandate)
    stage=body.stage or d['stage']
    if body.stage and body.stage!=d['stage']:
        await c.execute('update deals set stage=$2,updated_at=now() where id=$1',identity,stage)
        if d['prospect_id'] and stage!='archived_historical':await c.execute('update company_prospects set status=$2,updated_at=now() where id=$1',d['prospect_id'],{'initial_identification':'unqualified','contact_made':'contacted','vetting':'qualified','engagement_sent':'qualified','engaged_client':'client','completed':'client','archived_historical':'unqualified'}[stage])
    if stage!=d['stage'] or body.stage_note:
        await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',d['tenant_id'],identity,stage,body.stage_note,u['user_id'])
    before={'mandate_type':d['mandate_type'],'owner_user_id':str(d['owner_user_id']) if d['owner_user_id'] else None,'stage':d['stage'],'tasks':[],'activity':[],'contacts':[]}
    for task in body.tasks:
        if task.id:
            old=await c.fetchrow('select * from crm_tasks where id=$1 and deal_id=$2 for update',task.id,identity)
            if not old:raise HTTPException(404,'Next step not found in this deal')
            before['tasks'].append(dict(old))
            await c.execute('update crm_tasks set title=$2,assigned_to=$3,status=$4 where id=$1',task.id,task.title,task.assigned_to,task.status)
            if 'due_date' in task.model_fields_set:await c.execute('update crm_tasks set due_date=$2 where id=$1',task.id,task.due_date)
        else:await c.execute('insert into crm_tasks(tenant_id,deal_id,title,assigned_to,status,due_date) values($1,$2,$3,$4,$5,$6)',d['tenant_id'],identity,task.title,task.assigned_to,task.status,task.due_date)
    for activity in body.activity_edits:
        old=await c.fetchrow('select * from deal_stage_history where id=$1 and deal_id=$2 for update',activity.id,identity)
        if not old:raise HTTPException(404,'Update not found in this deal')
        before['activity'].append(dict(old))
        await c.execute('update deal_stage_history set note=$2 where id=$1',activity.id,activity.note)
    if body.activity:
        await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1,$2,$3,$4,$5::uuid)',d['tenant_id'],identity,stage,body.activity.kind.title()+': '+body.activity.summary,u['user_id'])
    for contact in body.contacts:
        if not d['prospect_id']:raise HTTPException(422,'Link a company prospect before editing company contacts')
        if not contact.id and not any((contact.name,contact.email,contact.phone)):raise HTTPException(422,'Enter a contact name, email or phone')
        if contact.id:
            old=await c.fetchrow('select * from prospect_contacts where id=$1 and prospect_id=$2 for update',contact.id,d['prospect_id'])
            if not old:raise HTTPException(404,'Contact not found in this company')
            if contact.email and await c.fetchval('select id from prospect_contacts where lower(email)=lower($1) and id<>$2',contact.email,contact.id):raise HTTPException(409,'That email is already linked to another contact')
            before['contacts'].append(dict(old))
            fields=[f for f in list(Contact.model_fields)+['notes'] if f in contact.model_fields_set]
            if ('first_name' in fields or 'last_name' in fields) and 'name' not in fields:fields.append('name')
            if fields:await c.execute('update prospect_contacts set '+','.join(f'{f}=${i+2}' for i,f in enumerate(fields))+' where id=$1',contact.id,*[getattr(contact,f) for f in fields])
        else:
            added=await insert_contact(c,u,'prospect',d['prospect_id'],contact)
            if contact.notes:await c.execute('update prospect_contacts set notes=$2 where id=$1',added['id'],contact.notes)
    await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'DEAL_WORKSPACE_SAVED','deal',str(identity),json.dumps({'before':before,'changes':body.model_dump(mode='json',exclude_unset=True)},default=str))
    return {'id':str(identity),'saved':True}
@router.patch('/deals/{identity}/workspace')
async def save_workspace(identity:UUID,body:WorkspaceSave,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:return await save_workspace_changes(c,u,identity,body)

@router.post('/deals/{identity}/company-profile')
async def link_company_profile(identity:UUID,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        d=await c.fetchrow('select * from deals where id=$1 for update',identity)
        if not d:raise HTTPException(404,'Deal not found')
        if d['prospect_id']:return {'prospect_id':str(d['prospect_id'])}
        await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':prospect:'+d['name'].casefold())
        p=await c.fetchrow('select id from company_prospects where lower(name)=lower($1)',d['name'])
        if p:
            if await c.fetchval('select id from deals where prospect_id=$1',p['id']):raise HTTPException(409,'This company already has another linked deal')
            pid=p['id']
        else:
            profile=d['funding_profile'];profile=json.loads(profile) if isinstance(profile,str) else dict(profile or {})
            keep={k:profile[k] for k in ('subcategory','currency','annual_revenue','ebitda','capital_sought','enterprise_value') if profile.get(k) is not None}
            pid=await c.fetchval('insert into company_prospects(tenant_id,name,sector,service_need,status,owner_user_id,company_profile) values($1,$2,$3,$4,$5,$6,$7::jsonb) returning id',d['tenant_id'],d['name'],profile.get('sector'),d['mandate_type'] if d['mandate_type'] in ('capital_raise','business_sale','both') else 'unknown',{'contact_made':'contacted','vetting':'qualified','engagement_sent':'qualified','engaged_client':'client'}.get(d['stage'],'unqualified'),d['owner_user_id'],json.dumps(keep))
        await ensure_deal(c,u,pid,identity)
        await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],'DEAL_COMPANY_LINKED','deal',str(identity),json.dumps({'prospect_id':str(pid)}))
        return {'prospect_id':str(pid)}

@router.get('/prospects/{identity}')
async def company_profile(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        p=await c.fetchrow('select * from company_prospects where id=$1',identity)
        if not p:raise HTTPException(404,'Company prospect not found')
        result=dict(p)
        if isinstance(result.get('company_profile'),str):result['company_profile']=json.loads(result['company_profile'])
        result['contacts']=[dict(x) for x in await c.fetch('select * from prospect_contacts where prospect_id=$1 order by is_primary desc,name',identity)]
        return result

from .inactive import router as inactive_router
router.include_router(inactive_router)
