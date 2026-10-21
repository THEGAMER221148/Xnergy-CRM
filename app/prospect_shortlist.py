"""Prospect investor assignments, explainable fit, and audience-specific CSV exports."""
import csv,io,json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Request,HTTPException,Query
from fastapi.responses import Response
from pydantic import BaseModel
from .db import tenant_tx
from .security import require_user,enforce_csrf
from .matching import rank_investors
from .pipeline import ensure_deal
from .crm import audit
router=APIRouter(prefix='/api')
READ=['admin','banker','deal_manager','compliance'];WRITE=['admin','banker','deal_manager']
def obj(v):return json.loads(v) if isinstance(v,str) else dict(v or {})
def safe_cell(v):
    s=str(v or '')
    return "'"+s if s.lstrip().startswith(('=','+','-','@')) or s.startswith(('\t','\r','\n')) else s

def export_csv(rows,audience):
    cols=['Investor','Website','Sector','Geography','Outreach status']
    if audience=='banker':cols+=['Relationship banker','Firm email','Firm phone']
    buf=io.StringIO(newline='');w=csv.writer(buf);w.writerow(cols)
    for r in rows:
        values=[r.get(k) for k in ['name','website_url','sector_focus','geography','status']]
        if audience=='banker':values += [r.get(k) for k in ['relationship_owner','firm_email','firm_phone']]
        w.writerow([safe_cell(x) for x in values])
    return '\ufeff'+buf.getvalue()

async def get_prospect(c,pid):
    p=await c.fetchrow('select * from company_prospects where id=$1',pid)
    if not p:raise HTTPException(404,'Company prospect not found')
    return p

@router.get('/prospects/{pid}/investor-matches')
async def matches(pid:UUID,request:Request,q:str=Query('',max_length=300),offset:int=Query(0,ge=0)):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        p=await get_prospect(c,pid)
        rows=await c.fetch("""select o.id,o.name,coalesce((select jsonb_agg(m.criteria || jsonb_build_object('id',m.id,'active',m.active)) from investor_mandates m where m.organization_id=o.id and m.active),'[]'::jsonb) mandates from investor_organizations o where ($1='' or o.name ilike '%'||$1||'%') order by o.name,o.id limit 201 offset $2""",q,offset)
    profile=obj(p['company_profile'])
    # No assumption that total capital sought equals an individual investor ticket.
    deal={'sector':p['sector'],'geography':p['geography'],'revenue':profile.get('annual_revenue'),'ebitda':profile.get('ebitda'),'currency':profile.get('currency')}
    investors=[{**dict(r),'mandates':json.loads(r['mandates']) if isinstance(r['mandates'],str) else r['mandates']} for r in rows[:200]]
    return {'company_name':p['name'],'items':rank_investors(deal,investors,include_excluded=True),'has_more':len(rows)>200,'offset':offset,'notice':'Ranked within this page of 200 investors. Search by firm name; unknown criteria are not confirmed matches.'}

class Assignment(BaseModel):
    organization_id:UUID
    status:Literal['shortlisted','contacted','interested','declined','removed']='shortlisted'

@router.post('/prospects/{pid}/investor-shortlist')
async def assign(pid:UUID,body:Assignment,request:Request):
    u=await require_user(request,WRITE);await enforce_csrf(request,u)
    async with tenant_tx(u) as c:
        await get_prospect(c,pid)
        if not await c.fetchval('select id from investor_organizations where id=$1',body.organization_id):raise HTTPException(404,'Investor not found')
        d=await ensure_deal(c,u,pid)
        # Assignment has no effect on document access or investor account permissions.
        identity=await c.fetchval('''insert into deal_investors(tenant_id,deal_id,organization_id,access_level,status) values($1::uuid,$2,$3,0,$4) on conflict(deal_id,organization_id) do update set status=excluded.status,updated_at=now() returning id''',u['tenant_id'],d['id'],body.organization_id,body.status)
        await audit(c,u,'PROSPECT_INVESTOR_ASSIGNED','deal_investor',identity)
    return {'status':body.status}

@router.get('/prospects/{pid}/investor-shortlist')
async def shortlist(pid:UUID,request:Request,audience:Literal['banker','client']='banker',download:bool=False):
    u=await require_user(request,READ)
    async with tenant_tx(u) as c:
        await get_prospect(c,pid)
        rows=await c.fetch('''select o.id,o.name,o.website_url,o.sector_focus,o.geography,o.relationship_owner,o.firm_email,o.firm_phone,di.status from deal_investors di join deals d on d.id=di.deal_id join investor_organizations o on o.id=di.organization_id where d.prospect_id=$1 and coalesce(di.status,'shortlisted')!='removed' order by o.name,o.id''',pid)
        if download:await audit(c,u,'SHORTLIST_EXPORTED_'+audience.upper(),'company_prospect',pid)
    if download:return Response(export_csv(rows,audience),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="investor-shortlist-{audience}.csv"','Cache-Control':'no-store'})
    return [dict(r) for r in rows]
