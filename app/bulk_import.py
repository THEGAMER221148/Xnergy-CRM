"""Deterministic spreadsheet intake; blank cells never become invented facts."""
import base64,csv,io,json,re
from decimal import Decimal,InvalidOperation
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field
from .investor_import import parse_rows,key,ALIASES
from .security import require_user,enforce_csrf
from .db import tenant_tx
router=APIRouter(prefix='/api')
PROSPECT_ALIASES={**ALIASES,'annual_revenue':{'annualrevenue','revenue','sales'},'ebitda':{'ebitda'},'capital_sought':{'capitalsought'},'enterprise_value':{'enterprisevalue'},'currency':{'currency'},'subcategory':{'subcategory'},'ownership':{'ownership','ownershiptype'},'company_size':{'companysize','employees'},'geography':{'geography'},'service_need':{'serviceneed','mandate'},'stage':{'stage','pipelinestage'},'street_address':{'streetaddress','address'},'city':{'city'},'state_region':{'state','stateregion'},'postal_code':{'zip','postalcode'},'country':{'country'}}
STAGES={'initial identification':'initial_identification','contact made':'contact_made','vetting':'vetting','engagement sent':'engagement_sent','signed client':'engaged_client','engaged client':'engaged_client'}
class Upload(BaseModel):
    filename:str=Field(min_length=1,max_length=255)
    data:str=Field(max_length=7*1024*1024)
    kind:Literal['prospect','investor']
    banker_id:UUID
    units:Literal['whole','thousands','millions']='whole'
    currency:Literal['USD','EUR','GBP','CAD','AUD']='USD'
    offset:int=Field(default=0,ge=0,le=10000)
    preview:bool=True

def amount(value,units):
    s=str(value).strip().replace(',','')
    if not s:return None
    negative=s.startswith('(') and s.endswith(')')
    if negative:s=s[1:-1]
    s=re.sub(r'^(USD|EUR|GBP|CAD|AUD)\s*','',s,flags=re.I)
    s=s.lstrip('$€£').strip()
    m=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(k|m|mm|b|million|thousand|billion)?',s,re.I)
    if not m:raise ValueError('Ambiguous financial amount: use a single number, not a range, percentage or estimate.')
    scale={'whole':1,'thousands':1000,'millions':1000000}
    suffix={'k':1000,'thousand':1000,'m':1000000,'mm':1000000,'million':1000000,'b':1000000000,'billion':1000000000}
    v=Decimal(m[1])*Decimal(suffix[m[2].lower()] if m[2] else scale[units])
    if negative:v=-v
    if abs(v)>Decimal('1e15'):raise ValueError('Financial amount exceeds supported range.')
    return float(v)

def prospect(row,body):
    from .crm import Prospect,Contact,CompanyProfile
    profile={k:row[k] for k in ('subcategory','ownership','company_size') if row.get(k)}
    for k in ('annual_revenue','ebitda','capital_sought','enterprise_value'):
        if row.get(k):profile[k]=amount(row[k],body.units)
    profile['currency']=row.get('currency') or body.currency
    stage=row.get('stage','').lower().replace('_',' ').strip()
    if stage and stage not in STAGES:raise ValueError('Unrecognized pipeline stage.')
    stage=STAGES.get(stage,'initial_identification')
    status={'contact_made':'contacted','vetting':'qualified','engagement_sent':'qualified','engaged_client':'client'}.get(stage,'unqualified')
    website=row.get('website_url') or None
    if website and not website.startswith(('https://','http://')):website='https://'+website
    notes='\n\n'.join(row[k] for k in ('description','notes') if row.get(k)) or None
    contact=Contact(name=row.get('contact_name') or None,email=row.get('email') or None,phone=row.get('phone') or None,title=row.get('title') or None)
    p=Prospect(name=row.get('firm_name',''),website_url=website,sector=row.get('sector_focus') or None,geography=row.get('geography') or None,service_need=row.get('service_need') or 'unknown',status=status,owner_user_id=body.banker_id,notes=notes,company_profile=CompanyProfile(**profile),**{k:row[k] for k in ('street_address','city','state_region','postal_code','country') if row.get(k)})
    return p,contact,stage

@router.post('/spreadsheet-import')
async def upload(request:Request):
    u=await require_user(request,['admin','banker','deal_manager']);await enforce_csrf(request,u)
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>8*1024*1024:raise HTTPException(413,'Maximum file size is 5 MB.')
    try:body=Upload.model_validate_json(raw)
    except Exception:raise HTTPException(422,'Check the file and choose a relationship banker.')
    async with tenant_tx(u) as c:
        banker=await c.fetchrow("select id,display_name from users where id=$1 and active and relationship_banker and role in ('admin','banker','deal_manager')",body.banker_id)
    if not banker:raise HTTPException(422,'Choose an active relationship banker.')
    digest,rows,ignored=parse_rows(body.filename,body.data,aliases=PROSPECT_ALIASES if body.kind=='prospect' else ALIASES,diagnostics=True)
    report={'ignored_columns':ignored,'total':len(rows),'created':0,'already_imported':0,'duplicates':0,'errors':[],'preview':[],'next_offset':None}
    subset=rows if body.preview else rows[body.offset:body.offset+50]
    from .crm import insert_contact,audit
    from .pipeline import ensure_deal
    for n,r in enumerate(subset,2 if body.preview else body.offset+2):
        try:
            if body.kind=='prospect':p,contact,stage=prospect(r,body)
            else:
                from .crm_models import Firm
                from .crm import Contact
                website=r.get('website_url') or None
                if website and not website.startswith(('https://','http://')):website='https://'+website
                Firm(name=r.get('firm_name',''),website_url=website,**{k:r[k] for k in ('description','firm_type','sector_focus','notes','firm_phone','firm_email') if r.get(k)})
                Contact(name=r.get('contact_name') or None,email=r.get('email') or None,phone=r.get('phone') or None)
            if body.preview:
                if len(report['preview'])<20:report['preview'].append({'row':n,'values':p.model_dump(mode='json') if body.kind=='prospect' else r})
                continue
            if body.kind=='investor':continue
            async with tenant_tx(u) as c:
                source='prospect-list:'+digest+':'+str(n)
                await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(u['tenant_id'])+':prospect:'+p.name.casefold())
                if await c.fetchval('select id from crm_source_records where source_key=$1',source):report['already_imported']+=1;continue
                if await c.fetchval('select id from company_prospects where lower(name)=lower($1)',p.name):
                    report['duplicates']+=1;report['errors'].append({'row':n,'message':'Company already exists; preserved existing record. Review before merging.'});continue
                fields=['name','website_url','sector','geography','service_need','status','owner_user_id','notes','street_address','city','state_region','postal_code','country']
                pid=await c.fetchval('insert into company_prospects(tenant_id,'+','.join(fields)+',company_profile) values($1::uuid,'+','.join('$'+str(i+2) for i in range(len(fields)))+',$15::jsonb) returning id',u['tenant_id'],*[getattr(p,k) for k in fields],p.company_profile.model_dump_json(exclude_none=True))
                if contact.name or contact.email or contact.phone:await insert_contact(c,u,'prospect',pid,contact)
                deal=await ensure_deal(c,u,pid)
                if deal['stage']!=stage:
                    await c.execute('update deals set stage=$2 where id=$1',deal['id'],stage)
                    await c.execute('insert into deal_stage_history(tenant_id,deal_id,stage,note,changed_by) values($1::uuid,$2,$3,$4,$5::uuid)',u['tenant_id'],deal['id'],stage,'Explicit spreadsheet stage',u['user_id'])
                await c.execute("insert into crm_source_records(tenant_id,prospect_id,source_key,filename,row_number,file_sha256,raw_record,normalized_record,status) values($1::uuid,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,'imported_prospect')",u['tenant_id'],pid,source,body.filename,n,digest,json.dumps(r),p.model_dump_json())
                await audit(c,u,'PROSPECT_LIST_IMPORTED','company_prospect',pid)
            report['created']+=1
        except Exception as e:
            report['errors'].append({'row':n,'message':str(e)[:300] if isinstance(e,ValueError) else 'Unable to import row; existing records preserved.'})
    if not body.preview and body.kind=='investor':
        from .investor_import import import_rows
        result=await import_rows(body.filename,body.data,u,banker_id=body.banker_id,offset=body.offset,limit=50)
        report.update(result)
    if not body.preview and body.offset+50<len(rows):report['next_offset']=body.offset+50
    report['banker']=banker['display_name']
    return report
