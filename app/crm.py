"""Internal CRM routes; all database access uses the authenticated tenant."""
import json
from uuid import UUID
from datetime import date
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from .crm_models import Firm, Record, Address, Criteria
from .db import tenant_tx
from .security import require_user, enforce_csrf

router = APIRouter(prefix='/api')
READ = ['admin', 'banker', 'deal_manager', 'compliance']
WRITE = ['admin', 'banker', 'deal_manager']

class Contact(Address):
    first_name: str | None = Field(default=None,max_length=150)
    last_name: str | None = Field(default=None,max_length=150)
    name: str | None = Field(default=None,max_length=300)
    title: str | None = Field(default=None,max_length=300)
    email: str | None = Field(default=None,max_length=320,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    phone: str | None = Field(default=None,max_length=100)
    linkedin_url: str | None = Field(default=None,max_length=2048)

    @field_validator('linkedin_url')
    @classmethod
    def valid_linkedin(cls,value):
        return Firm(name='Contact',linkedin_url=value).linkedin_url

    @field_validator('email',mode='before')
    @classmethod
    def normalize_email(cls,value):
        return value.strip().lower() if isinstance(value,str) and value.strip() else None

    @model_validator(mode='after')
    def display_name(self):
        if self.first_name or self.last_name:
            self.name=' '.join(x for x in (self.first_name,self.last_name) if x)
        return self

class ManagementContact(Contact):
    id: UUID | None = None
    is_primary: bool = False

class InvestorInput(Firm):
    investment_criteria: Criteria | None = None
    primary_contact: Contact | None = None

class CompanyProfile(Record):
    subcategory: str | None = Field(default=None,max_length=200)
    ownership: Literal['Family-owned','Privately held','Public','PE-backed','VC-backed','Other'] | None = None
    company_size: Literal['1–10','11–50','51–200','201–500','501–1,000','1,001+'] | None = None
    annual_revenue: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    ebitda: float | None = Field(default=None,allow_inf_nan=False)
    capital_sought: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    enterprise_value: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    currency: Literal['USD','EUR','GBP','CAD','AUD'] | None = None

class Prospect(Address):
    contacts: list[ManagementContact] = Field(default_factory=list,max_length=100)
    company_profile: CompanyProfile | None = None
    name: str = Field(min_length=1, max_length=300)
    website_url: str | None = None
    sector: str | None = Field(default=None, max_length=1000)
    geography: str | None = Field(default=None, max_length=1000)
    service_need: Literal['unknown','capital_raise','business_sale','both'] = 'unknown'
    status: Literal['unqualified','researching','contacted','qualified','not_a_fit','client'] = 'unqualified'
    owner_user_id: UUID | None = None
    primary_contact: Contact | None = None
    notes: str | None = Field(default=None, max_length=20000)

    @field_validator('website_url')
    @classmethod
    def validate_website(cls, value):
        return Firm(name='validation', website_url=value).website_url

class Note(Record):
    summary: str = Field(min_length=1, max_length=20000)
    interaction_type: Literal['note','call','meeting','email'] = 'note'

async def audit(c, u, event, kind, identity):
    await c.execute('select audit_event($1,$2,$3,$4,$5::jsonb)',u['user_id'],event,kind,str(identity),'{}')

@router.get('/stats')
async def stats(request: Request):
    u=await require_user(request, READ)
    async with tenant_tx(u) as c:
        return dict(await c.fetchrow('''select
          (select count(*) from investor_organizations) investor_orgs,
          (select count(*) from investor_contacts) investor_contacts,
          (select count(*) from investor_contacts where verified) verified_contacts,
          (select count(*) from investor_contacts where generic_email) generic_contacts,
          (select count(*) from interactions) interactions'''))

@router.get('/investors/{identity}')
async def investor_detail(identity: UUID, request: Request):
    u=await require_user(request, READ)
    async with tenant_tx(u) as c:
        org=await c.fetchrow('select * from investor_organizations where id=$1',identity)
        if not org: raise HTTPException(404,'Investor not found')
        contacts=await c.fetch('select * from investor_contacts where organization_id=$1 order by name,email',identity)
        history=await c.fetch('''select i.*,d.name deal_name from interactions i left join deals d on d.id=i.deal_id
            where i.organization_id=$1 order by i.occurred_at desc limit 100''',identity)
        deals=await c.fetch('''select di.*,d.name deal_name from deal_investors di join deals d on d.id=di.deal_id
            where di.organization_id=$1 order by di.updated_at desc''',identity)
    return {'organization':dict(org),'contacts':[dict(x) for x in contacts],
            'interactions':[dict(x) for x in history],'deals':[dict(x) for x in deals]}

@router.post('/investors',status_code=201)
async def create_investor(body: InvestorInput, request: Request):
    u=await require_user(request, WRITE); await enforce_csrf(request,u)
    # Explicit list prevents incoming payloads from assigning tenancy or ownership privileges.
    fields=list(Firm.model_fields)
    async with tenant_tx(u) as c:
        # Serializes repeated create requests for the same normalized name in this tenant.
        await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':investor:'+body.name.casefold())
        exists=await c.fetchval('select id from investor_organizations where lower(name)=lower($1)',body.name)
        if exists: raise HTTPException(409,{'message':'An investor with this name exists. Review it before adding another.','id':str(exists)})
        columns=','.join(fields)
        params=','.join('$'+str(i+2) for i in range(len(fields)))
        row=await c.fetchrow(f'insert into investor_organizations(tenant_id,{columns}) values($1::uuid,{params}) returning *',u['tenant_id'],*[getattr(body,k) for k in fields])
        if body.primary_contact: await insert_contact(c,u,'investor',row['id'],body.primary_contact)
        if body.investment_criteria:
            await c.execute("insert into investor_mandates(tenant_id,organization_id,name,criteria,evidence_status) values($1::uuid,$2,'Banker-entered criteria',$3::jsonb,'unverified')",u['tenant_id'],row['id'],body.investment_criteria.model_dump_json())
        await audit(c,u,'INVESTOR_CREATED','investor_organization',row['id'])
    return dict(row)

@router.post('/investors/{identity}/notes',status_code=201)
async def investor_note(identity: UUID, body: Note, request: Request):
    u=await require_user(request,WRITE); await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from investor_organizations where id=$1',identity): raise HTTPException(404,'Investor not found')
        row=await c.fetchrow('''insert into interactions(tenant_id,organization_id,interaction_type,summary,created_by)
            values($1::uuid,$2,$3,$4,$5::uuid) returning id''',u['tenant_id'],identity,body.interaction_type,body.summary,u['user_id'])
        await audit(c,u,'INVESTOR_NOTE_ADDED','interaction',row['id'])
    return dict(row)

@router.get('/team')
async def team(request: Request, all_members:bool=False):
    u=await require_user(request, READ)
    async with tenant_tx(u) as c:
        rows=await c.fetch("select id,display_name,email,relationship_banker from users where active and role in ('admin','banker','deal_manager') and ($1 or relationship_banker) order by display_name",all_members)
    return [dict(r) for r in rows]

@router.get('/prospects')
async def prospects(request: Request,added_from:date|None=None,added_to:date|None=None,sort:Literal['newest','oldest','name']='newest',q:str=Query('',max_length=300),status:str=Query('',max_length=40),sector:str='',subcategory:str='',ownership:str='',company_size:str='',service_need:str='',geography:str='',owner:str='',currency:str='',revenue_min:float|None=Query(None,ge=0),revenue_max:float|None=Query(None,ge=0),capital_min:float|None=Query(None,ge=0),capital_max:float|None=Query(None,ge=0),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
    if added_from and added_to and added_from>added_to:raise HTTPException(422,'Date added: start must not be after end')
    u=await require_user(request,READ)
    for low,high in [(revenue_min,revenue_max),(capital_min,capital_max)]:
        if low is not None and high is not None and low>high:raise HTTPException(422,'Minimum must not exceed maximum')
    async with tenant_tx(u) as c:
        rows=await c.fetch('''select p.*,u.display_name owner,(select id from deals where prospect_id=p.id) deal_id,(select stage from deals where prospect_id=p.id) pipeline_stage,
          (select coalesce(jsonb_agg(jsonb_build_object('id',ct.id,'is_primary',ct.is_primary,'name',ct.name,'first_name',ct.first_name,'last_name',ct.last_name,'title',ct.title,'email',ct.email,'phone',ct.phone,'linkedin_url',ct.linkedin_url)),'[]'::jsonb) from prospect_contacts ct where ct.prospect_id=p.id) contacts
          from company_prospects p left join users u on u.id=p.owner_user_id
          where ($1='' or concat_ws(' ',p.name,p.sector,p.geography) ilike '%'||$1||'%' or exists(select 1 from prospect_contacts pc where pc.prospect_id=p.id and concat_ws(' ',pc.name,pc.email,pc.phone) ilike '%'||$1||'%')) and ($2='' or p.status=$2)
          and ($5='' or p.sector=$5) and ($6='' or p.company_profile->>'subcategory'=$6)
          and ($7='' or p.company_profile->>'ownership'=$7) and ($8='' or p.company_profile->>'company_size'=$8)
          and ($9='' or p.service_need=$9) and ($10='' or p.geography ilike '%'||$10||'%')
          and ($11='' or u.display_name=$11 or exists(select 1 from crm_relationship_teams rt join users bu on bu.id=any(rt.banker_ids) where rt.entity_kind='prospect' and rt.entity_id=p.id and bu.display_name=$11)) and ($12='' or p.company_profile->>'currency'=$12)
          and ($13::numeric is null or (p.company_profile->>'annual_revenue')::numeric >= $13)
          and ($14::numeric is null or (p.company_profile->>'annual_revenue')::numeric <= $14)
          and ($15::numeric is null or (p.company_profile->>'capital_sought')::numeric >= $15)
          and ($16::numeric is null or (p.company_profile->>'capital_sought')::numeric <= $16)
          and ($17::date is null or p.created_at >= ($17::date::timestamp at time zone 'America/Los_Angeles'))
          and ($18::date is null or p.created_at < (($18::date+1)::timestamp at time zone 'America/Los_Angeles'))
          order by case when $19='newest' then p.created_at end desc,
          case when $19='oldest' then p.created_at end asc,p.name,p.id limit $3 offset $4''',q,status,limit,offset,sector,subcategory,ownership,company_size,service_need,geography,owner,currency,revenue_min,revenue_max,capital_min,capital_max,added_from,added_to,sort)
    result=[]
    for row in rows:
        r=dict(row)
        if isinstance(r.get('contacts'),str): r['contacts']=json.loads(r['contacts'])
        if isinstance(r.get('company_profile'),str): r['company_profile']=json.loads(r['company_profile'])
        from .prospect_fit import prospect_fit
        r['qualification']=prospect_fit(r)
        result.append(r)
    return result

@router.post('/prospects',status_code=201)
async def create_prospect(body: Prospect,request: Request):
    u=await require_user(request,WRITE); await enforce_csrf(request,u)
    website=body.website_url
    async with tenant_tx(u) as c:
        if body.owner_user_id and not await c.fetchval("select id from users where id=$1 and active and relationship_banker and role in ('admin','banker','deal_manager')",body.owner_user_id):
            raise HTTPException(422,'Choose an active banker in your team')
        await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':prospect:'+body.name.casefold())
        exists=await c.fetchval('select id from company_prospects where lower(name)=lower($1)',body.name)
        if exists: raise HTTPException(409,{'message':'A company with this name already exists.','id':str(exists)})
        row=await c.fetchrow('''insert into company_prospects(tenant_id,name,website_url,sector,geography,service_need,status,owner_user_id,notes,street_address,city,state_region,postal_code,country)
            values($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) returning *''',u['tenant_id'],body.name,website,body.sector,body.geography,body.service_need,body.status,body.owner_user_id,body.notes,body.street_address,body.city,body.state_region,body.postal_code,body.country)
        if body.company_profile:
            await c.execute('update company_prospects set company_profile=$2::jsonb where id=$1',row['id'],body.company_profile.model_dump_json(exclude_none=True))
        if body.primary_contact:
            added=await insert_contact(c,u,'prospect',row['id'],body.primary_contact)
            await c.execute('update prospect_contacts set is_primary=true where id=$1',added['id'])
        if sum(c.is_primary for c in body.contacts)+(1 if body.primary_contact else 0)>1:raise HTTPException(422,'Choose only one primary contact')
        for contact in body.contacts:
            added=await insert_contact(c,u,'prospect',row['id'],contact)
            if contact.is_primary:await c.execute('update prospect_contacts set is_primary=true where id=$1',added['id'])
        from .pipeline import ensure_deal
        await ensure_deal(c,u,row['id'])
        await audit(c,u,'PROSPECT_CREATED','company_prospect',row['id'])
    return dict(row)

class ProspectDetails(Address):
    name: str | None = Field(default=None,min_length=1,max_length=300)
    website_url: str | None = Field(default=None,max_length=2048)
    notes: str | None = Field(default=None,max_length=20000)
    owner_user_id: UUID | None = None
    contacts: list[ManagementContact] | None = Field(default=None,max_length=100)

    @field_validator('website_url')
    @classmethod
    def valid_website(cls,value):
        return Firm(name='Company',website_url=value).website_url

    sector: str | None = Field(default=None,max_length=1000)
    geography: str | None = Field(default=None,max_length=1000)
    service_need: Literal['unknown','capital_raise','business_sale','both'] = 'unknown'
    company_profile: CompanyProfile

@router.patch('/prospects/{identity}')
async def update_prospect_details(identity:UUID,body:ProspectDetails,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        row=await c.fetchrow('update company_prospects set sector=$2,geography=$3,service_need=$4,company_profile=$5::jsonb,updated_at=now() where id=$1 returning *',identity,body.sector,body.geography,body.service_need,body.company_profile.model_dump_json(exclude_none=True))
        if not row:raise HTTPException(404,'Company prospect not found')
        for key in ['name','website_url','notes','owner_user_id','street_address','city','state_region','postal_code','country']:
            if key in body.model_fields_set:
                value=getattr(body,key)
                if key=='name' and (not value or not value.strip()):raise HTTPException(422,'Company name is required')
                if key=='owner_user_id' and value and not await c.fetchval("select id from users where id=$1 and active and relationship_banker and role in ('admin','banker','deal_manager')",value):raise HTTPException(422,'Choose an active relationship banker')
                await c.execute(f'update company_prospects set {key}=$2 where id=$1',identity,value)
        if sum(c.is_primary for c in body.contacts or [])>1:raise HTTPException(422,'Choose only one primary contact')
        if body.contacts is not None:await c.execute('update prospect_contacts set is_primary=false where prospect_id=$1',identity)
        for contact in body.contacts or []:
            if contact.id:
                if not await c.fetchval('select id from prospect_contacts where id=$1 and prospect_id=$2',contact.id,identity):raise HTTPException(404,'Company contact not found')
                if contact.email and await c.fetchval('select id from prospect_contacts where lower(email)=lower($1) and id<>$2',contact.email,contact.id):raise HTTPException(409,'Email already belongs to another contact')
                if not any((contact.name,contact.email,contact.phone)):raise HTTPException(422,'Contact needs a name, email or phone')
                await c.execute('update prospect_contacts set name=$3,first_name=$4,last_name=$5,title=$6,email=$7,phone=$8,linkedin_url=$9 where id=$1 and prospect_id=$2',contact.id,identity,contact.name,contact.first_name,contact.last_name,contact.title,contact.email,contact.phone,contact.linkedin_url)
                if contact.is_primary:await c.execute('update prospect_contacts set is_primary=true where id=$1',contact.id)
            else:
                added=await insert_contact(c,u,'prospect',identity,contact)
                if contact.is_primary:await c.execute('update prospect_contacts set is_primary=true where id=$1',added['id'])
        row=await c.fetchrow('select * from company_prospects where id=$1',identity)
        await c.execute('update deals set name=$2,owner_user_id=$3,mandate_type=$4,updated_at=now() where prospect_id=$1',identity,row['name'],row['owner_user_id'],row['service_need'])
        await audit(c,u,'PROSPECT_DETAILS_UPDATED','company_prospect',identity)
    return dict(row)

@router.get('/deals/{identity}/investors')
async def deal_investors(identity: UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        rows=await c.fetch('''select di.*,o.name organization_name,
          (select count(*) from investor_contacts ct where ct.organization_id=o.id) contact_count
          from deal_investors di join investor_organizations o on o.id=di.organization_id
          where di.deal_id=$1 order by o.name''',identity)
    return [dict(r) for r in rows]

@router.get('/audit')
async def audit_list(request:Request):
    u=await require_user(request,['admin','deal_manager','compliance'])
    async with tenant_tx(u) as c: rows=await c.fetch('select * from audit_events order by created_at desc limit 100')
    return [dict(r) for r in rows]

class Task(Record):
    title: str = Field(min_length=1,max_length=500)
    assigned_to: UUID | None = None
    due_date: date | None = None
    organization_id: UUID | None = None
    prospect_id: UUID | None = None
    deal_id: UUID | None = None

class TaskStatus(Record):
    status: Literal['open','done','cancelled']

@router.get('/tasks')
async def tasks(request:Request,status:Literal['open','done','cancelled']='open',offset:int=Query(0,ge=0),assigned_to:UUID|None=None,unassigned:bool=False):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        rows=await c.fetch('''select t.*,u.display_name assignee,o.name investor,p.name prospect,d.name deal
          from crm_tasks t left join users u on u.id=t.assigned_to
          left join investor_organizations o on o.id=t.organization_id
          left join company_prospects p on p.id=t.prospect_id
          left join deals d on d.id=t.deal_id where t.status=$1 and t.task_kind='manual'
          and ($3::uuid is null or t.assigned_to=$3) and (not $4::boolean or t.assigned_to is null)
          order by t.due_date nulls last,t.created_at,t.id limit 100 offset $2''',status,offset,assigned_to,unassigned)
    return [dict(r) for r in rows]

@router.post('/tasks',status_code=201)
async def create_task(body:Task,request:Request):
    u=await require_user(request,WRITE); await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        for key,table in [('organization_id','investor_organizations'),('prospect_id','company_prospects'),('deal_id','deals'),('assigned_to','users')]:
            identity=getattr(body,key)
            if identity:
                clause=" and active and role in ('admin','banker','deal_manager')" if key=='assigned_to' else ''
                if not await c.fetchval(f'select id from {table} where id=$1'+clause,identity):
                    raise HTTPException(422,'Linked record or team member is unavailable')
        row=await c.fetchrow('''insert into crm_tasks(tenant_id,title,assigned_to,due_date,organization_id,prospect_id,deal_id)
          values($1::uuid,$2,$3,$4,$5,$6,$7) returning *''',u['tenant_id'],body.title,body.assigned_to,body.due_date,body.organization_id,body.prospect_id,body.deal_id)
        await audit(c,u,'TASK_CREATED','crm_task',row['id'])
    return dict(row)

@router.patch('/tasks/{identity}')
async def task_status(identity:UUID,body:TaskStatus,request:Request):
    u=await require_user(request,WRITE); await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        row=await c.fetchrow('update crm_tasks set status=$1 where id=$2 returning *',body.status,identity)
        if not row: raise HTTPException(404,'Task not found')
        await audit(c,u,'TASK_STATUS_CHANGED','crm_task',identity)
    return dict(row)

from .crm_models import Mandate

@router.get('/investors/{identity}/mandates')
async def investor_mandates(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from investor_organizations where id=$1',identity): raise HTTPException(404,'Investor not found')
        rows=await c.fetch('select * from investor_mandates where organization_id=$1 order by updated_at desc',identity)
    result=[]
    for row in rows:
        r=dict(row)
        for field in ('criteria','sources'):
            if isinstance(r[field],str): r[field]=json.loads(r[field])
        result.append(r)
    return result

@router.post('/investors/{identity}/mandates',status_code=201)
async def add_mandate(identity:UUID,body:Mandate,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from investor_organizations where id=$1',identity): raise HTTPException(404,'Investor not found')
        row=await c.fetchrow('''insert into investor_mandates(tenant_id,organization_id,name,criteria,evidence_status,sources,active,verified_at)
          values($1::uuid,$2,$3,$4::jsonb,$5,$6::jsonb,$7,case when $5='banker_verified' then now() else null end) returning id''',
          u['tenant_id'],identity,body.name,body.criteria.model_dump_json(),body.evidence_status,json.dumps(body.sources),body.active)
        await audit(c,u,'INVESTOR_MANDATE_ADDED','investor_mandate',row['id'])
    return dict(row)


async def insert_contact(c,u,kind,identity,body):
    if not any((body.name,body.email,body.phone)):
        raise HTTPException(422,'Enter a contact name, email address or phone number')
    table,link=('investor_contacts','organization_id') if kind=='investor' else ('prospect_contacts','prospect_id')
    if body.email:
        await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':'+kind+':contact:'+body.email)
        if await c.fetchval(f'select id from {table} where lower(email)=lower($1)',body.email):
            raise HTTPException(409,'This email address is already assigned to a contact. Review that contact before adding it again.')
    row=await c.fetchrow(f'insert into {table}(tenant_id,{link},name,title,email,phone,first_name,last_name,street_address,city,state_region,postal_code,country,linkedin_url) values($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) returning *',u['tenant_id'],identity,body.name,body.title,body.email,body.phone,body.first_name,body.last_name,body.street_address,body.city,body.state_region,body.postal_code,body.country,body.linkedin_url)
    await audit(c,u,'CONTACT_CREATED',kind+'_contact',row['id'])
    return dict(row)

@router.post('/investors/{identity}/contacts',status_code=201)
async def add_investor_contact(identity:UUID,body:Contact,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from investor_organizations where id=$1',identity):raise HTTPException(404,'Investor not found')
        return await insert_contact(c,u,'investor',identity,body)

@router.post('/prospects/{identity}/contacts',status_code=201)
async def add_prospect_contact(identity:UUID,body:Contact,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from company_prospects where id=$1',identity):raise HTTPException(404,'Company prospect not found')
        return await insert_contact(c,u,'prospect',identity,body)

@router.get('/imports')
async def import_review(request:Request,q:str=Query('',max_length=300),offset:int=Query(0,ge=0)):
    # Unclassified source rows can contain private notes: limit review to administrators.
    u=await require_user(request,['admin'])
    async with tenant_tx(u) as c:
        counts=await c.fetch('select status,count(*) count from crm_source_records group by status')
        rows=await c.fetch("""select id,filename,sheet_name,row_number,
          coalesce(nullif(normalized_record->>'filing_url',''),nullif(raw_record->>'filing_url',''),source_url) source_url,
          status,organization_id,prospect_id,
          coalesce(nullif(normalized_record->>'firm',''),nullif(normalized_record->>'name',''),nullif(normalized_record->>'firm_name',''),nullif(raw_record->>'name','')) firm,
          normalized_record->>'crm_category' category,
          coalesce(normalized_record->>'source',raw_record->>'source') source,
          coalesce(normalized_record->>'issuer_name',raw_record->>'issuer_name') issuer_name,
          coalesce(normalized_record->>'form',raw_record->>'form') form,
          coalesce(normalized_record->>'filing_date',raw_record->>'filing_date') filing_date
          from crm_source_records
          where status not in ('sec_import_complete','sec_issuer_metadata')
          and ($1='' or concat_ws(' ',filename,normalized_record->>'firm',normalized_record->>'name',normalized_record->>'firm_name',raw_record->>'name') ilike '%'||$1||'%')
          order by filename,sheet_name,row_number,id limit 100 offset $2""",q,offset)
    return {'counts':[dict(r) for r in counts],'rows':[dict(r) for r in rows]}

class ReferralSource(Record):
    firm_name: str = Field(min_length=1,max_length=300)
    first_name: str | None = Field(default=None,max_length=150)
    last_name: str | None = Field(default=None,max_length=150)
    email: str | None = Field(default=None,max_length=320,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    phone: str | None = Field(default=None,max_length=100)
    category: Literal['Attorney / law firm','CPA / accounting firm','Wealth manager / financial advisor','Bank / commercial lender','Business broker / M&A advisor','Consultant','Insurance advisor','Other']='Other'
    owner_user_id: UUID | None = None
    notes: str | None = Field(default=None,max_length=20000)

class ReferralUpdate(Record):
    owner_user_id: UUID | None = None
    notes: str | None = Field(default=None,max_length=20000)

async def validate_referral_owner(c,identity):
    if identity and not await c.fetchval("select id from users where id=$1 and active and role in ('admin','banker','deal_manager')",identity):
        raise HTTPException(422,'Choose an active banker in your team')

@router.get('/referral-sources')
async def referral_sources(request:Request,q:str=Query('',max_length=300),category:str=Query('',max_length=100),owner_user_id:UUID|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        rows=await c.fetch('''select r.*,o.display_name owner,s.display_name source_banker from referral_sources r
         left join users o on o.id=r.owner_user_id left join users s on s.id=r.sourced_by
         where ($1='' or concat_ws(' ',r.firm_name,r.first_name,r.last_name,r.email,r.phone,r.notes) ilike '%'||$1||'%')
         and ($2='' or r.category=$2) and ($3::uuid is null or r.owner_user_id=$3)
         order by r.firm_name,r.id limit $4 offset $5''',q,category,owner_user_id,limit,offset)
    return [dict(r) for r in rows]

@router.post('/referral-sources',status_code=201)
async def create_referral_source(body:ReferralSource,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        await validate_referral_owner(c,body.owner_user_id)
        fields=list(ReferralSource.model_fields)
        values=[getattr(body,k) for k in fields]
        row=await c.fetchrow('insert into referral_sources(tenant_id,sourced_by,'+','.join(fields)+') values($1::uuid,$2::uuid,'+','.join('$'+str(i+3) for i in range(len(fields)))+') returning *',u['tenant_id'],u['user_id'],*values)
        await audit(c,u,'REFERRAL_SOURCE_CREATED','referral_source',row['id'])
    return dict(row)

@router.patch('/referral-sources/{identity}')
async def update_referral_source(identity:UUID,body:ReferralUpdate,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    fields=list(body.model_fields_set)
    if not fields:raise HTTPException(422,'No changes provided')
    async with tenant_tx(u) as c:
        if 'owner_user_id' in fields:await validate_referral_owner(c,body.owner_user_id)
        row=await c.fetchrow('update referral_sources set '+','.join(k+'=$'+str(i+2) for i,k in enumerate(fields))+',updated_at=now() where id=$1 returning *',identity,*[getattr(body,k) for k in fields])
        if not row:raise HTTPException(404,'Referral source not found')
        await audit(c,u,'REFERRAL_SOURCE_UPDATED','referral_source',identity)
    return dict(row)

from .prototype_search import InvestorSearch, match_criteria

@router.post('/investor-search')
async def prototype_investor_search(body:InvestorSearch,request:Request):
    u=await require_user(request,READ)
    if body.minimum is not None and body.maximum is not None and body.minimum>body.maximum:
        raise HTTPException(422,'Minimum investment must not exceed maximum')
    # Cursor is bounded in memory; filters apply to the same contact and mandate.
    result=[];matched=0
    async with tenant_tx(u) as c:
        # Sort organizations before fetching contacts/mandates. Correlated aggregates
        # before a full sort become prohibitively expensive for large SEC imports.
        query="""select o.* from investor_organizations o
        where o.tenant_id=$4::uuid and ($1='' or o.name ilike '%'||$1||'%')
        and ($2='' or coalesce(o.relationship_owner,'Unassigned')=$2 or exists(select 1 from crm_relationship_teams rt join users bu on bu.id=any(rt.banker_ids) where rt.entity_kind='investor' and rt.entity_id=o.id and bu.display_name=$2) or exists(
          select 1 from crm_source_records sr where sr.tenant_id=$4::uuid
          and sr.organization_id=o.id and sr.normalized_record->>'source_banker'=$2))
        and ($5='' or o.observed_holding_sectors ilike '%'||$5||'%')
        and (($6='' and $7='' and $8='') or exists(
          select 1 from investor_contacts ct where ct.tenant_id=$4::uuid and ct.organization_id=o.id
          and ($6='' or ct.name ilike '%'||$6||'%') and ($7='' or ct.email::text ilike '%'||$7||'%')
          and ($8='' or ct.phone ilike '%'||$8||'%')))
        order by case when exists(select 1 from unnest($3::text[]) wanted where lower(coalesce(o.observed_holding_sectors,'')) like '%'||lower(wanted)||'%' or lower(coalesce(o.sector_focus,'')) like '%'||lower(wanted)||'%') then 0 else 1 end,o.name,o.id"""
        async def candidates():
            cursor=await c.cursor(query,body.firm,body.banker,body.selections.sector,
                                  UUID(str(u['tenant_id'])),body.holding_sector,body.contact,body.email,body.phone)
            while batch:=await cursor.fetch(100):
                ids=[r['id'] for r in batch]
                contact_map={};mandate_map={}
                for ct in await c.fetch('select id,organization_id,name,email,phone from investor_contacts where tenant_id=$1::uuid and organization_id=any($2::uuid[])',u['tenant_id'],ids):
                    contact_map.setdefault(ct['organization_id'],[]).append(dict(ct))
                for m in await c.fetch('select organization_id,criteria,evidence_status from investor_mandates where tenant_id=$1::uuid and organization_id=any($2::uuid[]) and active',u['tenant_id'],ids):
                    item=dict(m)
                    if isinstance(item['criteria'],str):item['criteria']=json.loads(item['criteria'])
                    mandate_map.setdefault(m['organization_id'],[]).append(item)
                for row in batch:yield dict(row),contact_map.get(row['id'],[]),mandate_map.get(row['id'],[])
        async for org,contacts,mandates in candidates():
            contact_filters=[('name',body.contact),('email',body.email),('phone',body.phone)]
            matching_contacts=[ct for ct in contacts if all(not v or v.casefold() in (ct.get(k) or '').casefold() for k,v in contact_filters)]
            if any(v for _,v in contact_filters) and not matching_contacts:continue
            candidates=[]
            for mandate in mandates or [{'criteria':{},'evidence_status':'unverified'}]:
                fit=match_criteria(org,mandate['criteria'],body)
                if fit is not None:candidates.append((fit,mandate))
            if not candidates:continue
            fit,mandate=min(candidates,key=lambda x:len(x[0]['missing']))
            if matched>=body.offset:
                result.append({'organization':org,'contacts':matching_contacts or contacts,'criteria':mandate['criteria'],'evidence_status':mandate['evidence_status'],**fit})
                if len(result)>body.limit:break
            matched+=1
    return {'rows':result[:body.limit],'has_more':len(result)>body.limit}

class SavedInvestorSearch(Record):
    name:str=Field(min_length=1,max_length=80)
    filters:InvestorSearch

@router.get('/saved-investor-searches')
async def saved_investor_searches(request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        rows=await c.fetch('select id,name,filters from crm_saved_searches where created_by=$1::uuid order by created_at desc,id limit 200',u['user_id'])
    result=[]
    for row in rows:
        r=dict(row)
        if isinstance(r['filters'],str):r['filters']=json.loads(r['filters'])
        result.append(r)
    return result

@router.post('/saved-investor-searches',status_code=201)
async def save_investor_search(body:SavedInvestorSearch,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    filters=body.filters.model_copy(update={'offset':0})
    async with tenant_tx(u) as c:
        row=await c.fetchrow('insert into crm_saved_searches(tenant_id,created_by,name,filters) values($1::uuid,$2::uuid,$3,$4::jsonb) returning id',u['tenant_id'],u['user_id'],body.name,filters.model_dump_json())
        await audit(c,u,'INVESTOR_SEARCH_SAVED','crm_saved_search',row['id'])
    return dict(row)


@router.get('/deals/{identity}/outreach-history')
async def deal_outreach_history(identity:UUID,request:Request,offset:int=Query(0,ge=0),filename:str=Query('',max_length=500)):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from deals where id=$1',identity):
            raise HTTPException(404,'Deal not found')
        sources=await c.fetch("""select filename,sheet_name,count(*) row_count
          from crm_source_records where tenant_id=$1::uuid and status='imported_investor'
          and normalized_record->'client_outreach_context'->>'deal_id'=$2
          group by filename,sheet_name order by filename,sheet_name""",u['tenant_id'],str(identity))
        rows=await c.fetch("""select id,organization_id,filename,sheet_name,row_number,
          normalized_record from crm_source_records where tenant_id=$1::uuid
          and status='imported_investor' and normalized_record->'client_outreach_context'->>'deal_id'=$2
          and ($3='' or filename=$3) order by filename,sheet_name,row_number,id limit 101 offset $4""",u['tenant_id'],str(identity),filename,offset)
    history=[]
    for row in rows[:100]:
        r=dict(row);n=r.pop('normalized_record')
        if isinstance(n,str):n=json.loads(n)
        r.update(firm=n.get('firm'),contact=n.get('contact'),email=n.get('email'),phone=n.get('phone'),
          source_banker=n.get('source_banker'),notes=n.get('notes'),source_fields=n.get('extra',{}))
        history.append(r)
    return {'sources':[dict(r) for r in sources],'rows':history,'has_more':len(rows)>100}

@router.get('/today')
async def today(request:Request,offset:int=Query(0,ge=0)):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        counts=await c.fetchrow("select count(*) filter(where status='open') open_tasks,count(*) filter(where status='open' and due_date<current_date) overdue from crm_tasks where task_kind='manual' and assigned_to=$1::uuid",u['user_id'])
        rows=await c.fetch("select t.id,t.title,t.due_date,t.status,t.organization_id,coalesce(t.prospect_id,d.prospect_id) prospect_id,t.deal_id,coalesce(o.name,p.name,d.name) company_name from crm_tasks t left join investor_organizations o on o.id=t.organization_id left join company_prospects p on p.id=t.prospect_id left join deals d on d.id=t.deal_id where t.task_kind='manual' and t.assigned_to=$1::uuid and t.status='open' order by t.due_date nulls last,t.created_at,t.id limit 100 offset $2",u['user_id'],offset)
        people=await c.fetch("select id,display_name,email,role from users where active and role<>'investor' order by display_name")
    return {'counts':dict(counts),'tasks':[dict(x) for x in rows],'people':[dict(x) for x in people]}

# Investor profile editing and evidence-backed website research.
from datetime import datetime
import hashlib
from .crm_models import Mandate

class InvestorEditContact(Contact):
    id: UUID | None = None

class InvestorEditMandate(Mandate):
    id: UUID | None = None

class InvestorEdit(Record):
    revision: str
    organization: Firm
    contacts: list[InvestorEditContact] = Field(default_factory=list,max_length=100)
    mandates: list[InvestorEditMandate] = Field(default_factory=list,max_length=100)
    research_sources: list[str] = Field(default_factory=list,max_length=30)
    research_summary: str | None = Field(default=None,max_length=20000)
    research_reviewed: bool = False
    research_job_id: UUID | None = None

    @model_validator(mode='after')
    def validate_sources(self):
        for source in self.research_sources: Firm(name='Source',website_url=source)
        for items in (self.contacts,self.mandates):
            ids=[x.id for x in items if x.id]
            if len(ids)!=len(set(ids)):raise ValueError('Duplicate record IDs')
        emails=[x.email for x in self.contacts if x.email]
        if len(emails)!=len(set(emails)):raise ValueError('Duplicate contact email')
        return self

async def investor_edit_snapshot(c,identity,lock=False):
    org=await c.fetchrow('select * from investor_organizations where id=$1'+(' for update' if lock else ''),identity)
    if not org:raise HTTPException(404,'Investor not found')
    contacts=[dict(x) for x in await c.fetch('select * from investor_contacts where organization_id=$1 order by id'+(' for update' if lock else ''),identity)]
    mandates=[dict(x) for x in await c.fetch('select * from investor_mandates where organization_id=$1 order by id'+(' for update' if lock else ''),identity)]
    for m in mandates:
        for key in ('criteria','sources'):
            if isinstance(m[key],str):m[key]=json.loads(m[key])
    result={'organization':dict(org),'contacts':contacts,'mandates':mandates}
    result['revision']=hashlib.sha256(json.dumps(result,default=str,sort_keys=True).encode()).hexdigest()
    return result

@router.get('/investors/{identity}/profile-editor')
async def investor_editor(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:return await investor_edit_snapshot(c,identity)

@router.put('/investors/{identity}/profile-editor')
async def save_investor_editor(identity:UUID,body:InvestorEdit,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    return await persist_investor_editor(identity,body,u)

async def persist_investor_editor(identity,body,u):
    async with tenant_tx(u) as c:
        current=await investor_edit_snapshot(c,identity,True)
        if body.revision!=current['revision']:raise HTTPException(409,'This investor changed while you were editing. Reopen the profile before saving.')
        contact_ids={str(x['id']) for x in current['contacts']}
        mandate_ids={str(x['id']) for x in current['mandates']}
        if any(x.id and str(x.id) not in contact_ids for x in body.contacts) or any(x.id and str(x.id) not in mandate_ids for x in body.mandates):raise HTTPException(422,'A contact or mandate does not belong to this investor.')
        fields=list(Firm.model_fields)
        await c.execute('update investor_organizations set '+','.join(k+'=$'+str(i+2) for i,k in enumerate(fields))+',updated_at=now() where id=$1',identity,*[getattr(body.organization,k) for k in fields])
        for contact in body.contacts:
            # Imported contact channels must not disappear when an empty form is submitted.
            previous=next((x for x in current['contacts'] if str(x['id'])==str(contact.id)),None)
            if previous:
                for key in ('email','phone'):
                    if not getattr(contact,key) and previous.get(key):setattr(contact,key,previous[key])
            if not any((contact.name,contact.email,contact.phone)):raise HTTPException(422,'Enter a contact name, email or phone.')
            if not contact.id:
                await insert_contact(c,u,'investor',identity,Contact.model_validate(contact.model_dump(exclude={'id'})))
                continue
            if contact.email:
                await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':investor:contact:'+contact.email)
                if await c.fetchval('select id from investor_contacts where lower(email)=lower($1) and id<>$2',contact.email,contact.id):raise HTTPException(409,'This email is already assigned to another contact. No changes were saved.')
            fields=list(Contact.model_fields)
            await c.execute('update investor_contacts set '+','.join(k+'=$'+str(i+3) for i,k in enumerate(fields))+' where id=$1 and organization_id=$2',contact.id,identity,*[getattr(contact,k) for k in fields])
        for m in body.mandates:
            args=[m.name,m.criteria.model_dump_json(),m.evidence_status,json.dumps(m.sources),m.active]
            if m.id:
                await c.execute('update investor_mandates set name=$3,criteria=$4::jsonb,evidence_status=$5,sources=$6::jsonb,active=$7,updated_at=now(),verified_at=case when $5=\'banker_verified\' then now() else null end where id=$1 and organization_id=$2',m.id,identity,*args)
            else:
                await c.execute('insert into investor_mandates(tenant_id,organization_id,name,criteria,evidence_status,sources,active) values($1::uuid,$2,$3,$4::jsonb,$5,$6::jsonb,$7)',u['tenant_id'],identity,*args)
        if body.research_reviewed:
            missing=[]
            if not body.organization.description:missing.append('description')
            if not body.organization.sector_focus and not any(m.active and m.criteria.sectors for m in body.mandates):missing.append('sector focus')
            if not any(m.active and m.criteria.stages for m in body.mandates):missing.append('investment stage')
            if not any(m.active and m.criteria.check_min is not None and m.criteria.check_max is not None and m.criteria.currency for m in body.mandates):missing.append('investment size and currency')
            if not body.contacts or any(not x.name or not x.email or not x.phone for x in body.contacts):missing.append('contact information')
            await c.execute('update investor_organizations set enrichment_status=$2,last_researched_at=now() where id=$1',identity,'needs_follow_up' if missing else 'reviewed')
            summary='Website research reviewed. Sources: '+', '.join(body.research_sources)+'\n'+(body.research_summary or '')
            if missing:
                summary+='\nFollow up: '+', '.join(missing)
                title='Complete investor profile: '+body.organization.name
                if not await c.fetchval("select id from crm_tasks where organization_id=$1 and title=$2 and status='open'",identity,title):
                    await c.execute("insert into crm_tasks(tenant_id,organization_id,assigned_to,title,task_kind) values($1::uuid,$2,$3::uuid,$4,'research')",u['tenant_id'],identity,u['user_id'],title)
            await c.execute("insert into interactions(tenant_id,organization_id,interaction_type,summary,created_by) values($1::uuid,$2,'note',$3,$4::uuid)",u['tenant_id'],identity,summary,u['user_id'])
        if body.research_reviewed and any(x.name and x.email and x.phone for x in body.contacts):
            await c.execute("update crm_tasks set status='done' where organization_id=$1 and status='open' and title like 'Find contact details:%'",identity)
        await audit(c,u,'INVESTOR_PROFILE_UPDATED','investor_organization',identity)
        return await investor_edit_snapshot(c,identity)

class InvestorResearch(Record):
    website_url: str | None = Field(default=None,max_length=2048)

@router.post('/investors/{identity}/research')
async def research_investor(identity:UUID,body:InvestorResearch,request:Request):
    from .investor_research import research_profile
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c: current=await investor_edit_snapshot(c,identity)
    return await research_profile(current,body.website_url,u)

@router.get('/investors/{identity}/research-proposals')
async def investor_research_proposals(identity:UUID,request:Request):
    # Automated research is saved directly; this endpoint keeps the manual editor compatible.
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from investor_organizations where id=$1',identity):raise HTTPException(404,'Investor not found')
    return []

@router.get('/investor-research-status')
async def investor_research_status(request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        counts=await c.fetch('select status,count(*) total from investor_research_jobs group by status')
        populated=await c.fetchval("select count(*) from investor_research_jobs where result_summary='Supported missing fields populated; existing values preserved.'")
        sec=await c.fetchrow("select count(*) imported,count(*) filter(where observed_holding_sectors is not null) sectors_populated from investor_organizations where source='SEC'")
        quarters=await c.fetchval("select count(*) from crm_source_records where source_key like 'sec:quarter:%' and status='sec_import_complete'")
    from .research_sources import coverage
    return {**{r['status']:r['total'] for r in counts},'populated':populated,'sources':coverage(),'sec_import':{**dict(sec),'quarters_imported':quarters,'quarters_expected':12,'requested_start':'2023-09-22','requested_end':'2026-09-22'}}

@router.post('/investor-research-queue')
async def queue_investor_research(request:Request):
    u=await require_user(request,['admin']);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        # RLS scopes this queue to the authenticated company's database.
        result=await c.execute('''insert into investor_research_jobs(tenant_id,organization_id,requested_by)
          select tenant_id,id,$1::uuid from investor_organizations
          on conflict(tenant_id,organization_id) do nothing''',u['user_id'])
        await audit(c,u,'INVESTOR_RESEARCH_QUEUED','tenant',u['tenant_id'])
    return {'queued':int(result.split()[-1]),'message':'Website research queued. Supported missing fields will be filled automatically; uncertain matches go to follow-up.'}

class InvestorListUpload(Record):
    filename: str = Field(min_length=1,max_length=255)
    data: str = Field(max_length=7*1024*1024)

@router.post('/investor-list-import')
async def investor_list_import(request:Request):
    from .investor_import import import_rows
    from .security import rate_limit
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    await rate_limit('investor-import:'+str(u['user_id']),5,3600)
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>8*1024*1024:raise HTTPException(413,'Choose a file up to 5 MB.')
    try:body=InvestorListUpload.model_validate_json(raw)
    except Exception:raise HTTPException(422,'Choose a valid CSV or XLSX investor list.')
    return await import_rows(body.filename,body.data,u)

@router.get('/ai-work')
async def ai_work(request:Request,view:Literal['working','input','finished','paused']='working',q:str=Query('',max_length=200),offset:int=Query(0,ge=0)):
    u=await require_user(request,READ)
    condition={'working':"not j.paused and j.status in ('pending','running')",'input':"not j.paused and j.status in ('needs_follow_up','failed')",'finished':"j.status='completed'",'paused':"j.paused"}[view]
    async with tenant_tx(u) as c:
        rows=await c.fetch(f'''select j.id,j.organization_id,o.name,j.status,j.paused,j.attempts,j.result_summary,j.started_at,j.finished_at,
          (j.saved_changes is not null) has_results from investor_research_jobs j
          join investor_organizations o on o.id=j.organization_id and o.tenant_id=j.tenant_id
          where {condition} and ($1='' or o.name ilike '%'||$1||'%')
          order by j.priority desc,j.created_at,j.id limit 26 offset $2''',q.strip(),offset)
    return {'items':[dict(r) for r in rows[:25]],'has_more':len(rows)>25,'can_edit':u['role'] in WRITE}

@router.get('/ai-work/{identity}')
async def ai_work_result(identity:UUID,request:Request):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        row=await c.fetchrow('''select j.*,o.name from investor_research_jobs j join investor_organizations o
          on o.id=j.organization_id and o.tenant_id=j.tenant_id where j.id=$1''',identity)
    if not row:raise HTTPException(404,'Research task not found')
    return dict(row)

class AIWorkAction(Record):
    action: Literal['run','pause']

@router.post('/ai-work/{identity}')
async def ai_work_action(identity:UUID,body:AIWorkAction,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        row=await c.fetchrow('select * from investor_research_jobs where id=$1 for update',identity)
        if not row:raise HTTPException(404,'Research task not found')
        if row['status']=='running':raise HTTPException(409,'Research is already running. Wait for this attempt to finish.')
        if body.action=='pause':
            if row['status']!='pending':raise HTTPException(409,'Only queued research can be paused.')
            await c.execute('update investor_research_jobs set paused=true where id=$1',identity)
        else:
            await c.execute("update investor_research_jobs set status='pending',paused=false,priority=1,requested_by=$2::uuid where id=$1",identity,u['user_id'])
        await audit(c,u,'AI_RESEARCH_'+body.action.upper(),'investor_research_job',identity)
    return {'message':'Research paused.' if body.action=='pause' else 'Prioritized for the next available research worker. Existing results are preserved.'}

@router.post('/tasks/{identity}/assign-ai')
async def assign_task_ai(identity:UUID,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        task=await c.fetchrow("select * from crm_tasks where id=$1 and status='open' for update",identity)
        if not task:raise HTTPException(404,'Open task not found')
        if not task['organization_id']:raise HTTPException(422,'Link this task to an investor before assigning investor research.')
        job=await c.fetchrow('''insert into investor_research_jobs(tenant_id,organization_id,requested_by,priority)
          values($1::uuid,$2,$3::uuid,1) on conflict(tenant_id,organization_id) do update set
          priority=1,paused=false,status=case when investor_research_jobs.status='running' then 'running' else 'pending' end,
          requested_by=case when investor_research_jobs.status='running' then investor_research_jobs.requested_by else excluded.requested_by end
          returning id''',u['tenant_id'],task['organization_id'],u['user_id'])
        await c.execute("update crm_tasks set task_kind='research' where id=$1",identity)
        await audit(c,u,'TASK_ASSIGNED_AI','crm_task',identity)
    return {'id':str(job['id']),'message':'Investor research assigned to AI. The original task remains open until its work is resolved.'}
