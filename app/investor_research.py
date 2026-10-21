"""Read-only research proposals for an existing investor, using public website evidence."""
import asyncio
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import intake
from .intake_sources import website_research,IntakeSourceError
from .security import rate_limit

PERSONAL_DOMAINS={'gmail.com','googlemail.com','yahoo.com','hotmail.com','outlook.com','live.com','aol.com','icloud.com','me.com','msn.com','proton.me','protonmail.com','mail.com','ymail.com'}

def website_candidate(profile,override=None):
    org=profile['organization']
    if override:return override,'Entered website'
    if org.get('website_url'):return org['website_url'],'Saved website'
    candidates=[]
    if org.get('domain'):candidates.append(org['domain'])
    for email in [org.get('firm_email')]+[c.get('email') for c in profile['contacts']]:
        if email and '@' in email:candidates.append(email.rsplit('@',1)[1])
    for domain in candidates:
        domain=domain.strip().lower()
        if domain not in PERSONAL_DOMAINS and '.' in domain and all(c.isalnum() or c in '.-' for c in domain):return 'https://'+domain,'Email/domain lead — confirm this is the correct firm'
    raise HTTPException(422,'Enter the firm website. A personal email address does not identify its company website.')

async def research_profile(profile,override,user,background=False):
    if not intake.configured():raise HTTPException(503,'AI website research is not configured. You can still edit this profile manually.')
    from .research_sources import sec_match,sec_record,website
    sec=sec_match(profile)
    warnings=[];pages=[];url=None
    try:url,basis=website_candidate(profile,override)
    except HTTPException:
        url=website(sec.get('website_url')) if sec else None
        basis='SEC Form ADV exact firm match' if sec else 'No identified firm website'
    # Prefer the SEC-published website over an unverified email-domain lead.
    if sec and not override and not profile['organization'].get('website_url') and website(sec.get('website_url')):
        url=website(sec['website_url']);basis='SEC Form ADV exact firm match'
    if not url and not sec:raise HTTPException(422,'No unique SEC adviser match or identified business website. Confirm the firm website for further research.')
    if background:
        await rate_limit('investor-research:tenant:'+str(user['tenant_id']),1500,86400)
    else:
        await rate_limit('intake:user:'+str(user['user_id']),6,60)
        await rate_limit('intake:tenant:'+str(user['tenant_id']),100,86400)
    if url:
        try:pages,warnings=await asyncio.wait_for(website_research(url),75)
        except Exception:
            if not sec:raise HTTPException(422,'Could not read this website. Enter a specific firm, strategy or contact page and try again.')
            warnings.append('Firm website could not be read; only matched SEC contact details are available.')
    sources=[p['url'] for p in pages]
    if sec:sources.extend([sec['source'],'https://adviserinfo.sec.gov/firm/summary/'+sec['crd']])
    if not pages:
        return {'records':[sec_record(sec,profile['organization']['name'])],'sources':sources,'website_url':url,'basis':basis,'source_warnings':warnings,'saved':False,'review_required':True}
    # Existing names are matching hints, not evidence for invented contact details or criteria.
    names=', '.join(str(c.get('name') or '') for c in profile['contacts'])
    content=[{'type':'input_text','text':'Research target (matching hints only, not source evidence): '+profile['organization']['name']+'. Existing contacts: '+names+'. Extract this firm description, published investment criteria and sector focus, and named contacts. A website reached from an email domain may belong to a different company: identify the firm actually described by the sources. Leave unpublished values null.'}]
    content += [{'type':'input_text','text':'Public website source: '+p['url']+'\n'+p['text']} for p in pages]
    result=await intake.extract(content)
    # Do not save contact channels that cannot be found in the fetched material.
    import re
    source_text="\n".join(p["text"] for p in pages).lower()
    source_digits=re.sub(r"[^0-9]","",source_text)
    for record in result.records:
        for key in ("email","firm_email"):
            value=getattr(record,key)
            if value and value.lower() not in source_text:setattr(record,key,None)
        for key in ("phone","firm_phone"):
            value=getattr(record,key)
            if value and re.sub(r"[^0-9]","",value) not in source_digits:setattr(record,key,None)
    output=result.model_dump()
    matched_website=any(matching_name(r.get('firm_name'))==matching_name(profile['organization']['name']) and r.get('category')=='investor_buyer' for r in output['records'])
    chosen_url=pages[0]['url'] if matched_website else website(sec.get('website_url')) if sec else None
    if sec:output['records'].append(sec_record(sec,profile['organization']['name']))
    return {**output,'sources':sources,'website_url':chosen_url,'basis':basis,'source_warnings':warnings,'saved':False,'review_required':True}

def matching_name(value):
    import re
    words=re.sub(r'[^a-z0-9 ]',' ',str(value or '').lower()).split()
    return ''.join(w for w in words if w not in {'llc','llp','inc','ltd','limited','the','corp','corporation'})

def automatic_edit(profile,proposal):
    """Only fill blanks on a positively named firm; never replace existing values."""
    from .crm import InvestorEdit,Contact
    from .crm_models import Firm,Criteria
    from copy import deepcopy
    org={k:profile['organization'].get(k) for k in Firm.model_fields}
    contacts=[{k:c.get(k) for k in ['id',*Contact.model_fields]} for c in profile['contacts']]
    mandates=[{k:deepcopy(m[k]) for k in ('id','name','criteria','evidence_status','sources','active')} for m in profile['mandates']]
    target=matching_name(org['name'])
    matches=[r for r in proposal.get('records',[]) if matching_name(r.get('firm_name'))==target and target and r.get('category')=='investor_buyer']
    if not matches:raise ValueError('Could not confidently match the website to this investor. Confirm its official website and category.')
    notes=list(proposal.get('source_warnings',[]))
    for r in matches:
        for key,source in {'description':'description','firm_type':'investor_type','firm_email':'firm_email','firm_phone':'firm_phone','sector_focus':'sector','geography':'investment_geography','street_address':'street_address','city':'city','state_region':'state_region','postal_code':'postal_code','country':'country'}.items():
            if not org.get(key) and r.get(source):org[key]=r[source]
        if not org.get('website_url'):org['website_url']=proposal['website_url']
        name=' '.join(x for x in (r.get('first_name'),r.get('last_name')) if x)
        contact=next((c for c in contacts if r.get('email') and c.get('email') and c['email'].lower()==r['email'].lower()),None)
        if contact is None and name:contact=next((c for c in contacts if matching_name(c.get('name'))==matching_name(name)),None)
        # A published named person at the positively matched firm can become a new contact.
        if contact is None and name and r.get('email'):
            contact={'name':name,'email':r['email']};contacts.append(contact)
        if contact is not None:
            for key in ('title','email','phone','linkedin_url'):
                if not contact.get(key) and r.get(key):contact[key]=r[key]
            if not contact.get('name') and name:contact['name']=name
        values={}
        for key,source in {'sectors':'sector','geographies':'investment_geography','financing_types':'financing_type','stages':'investment_stage','transaction_types':'transaction_type'}.items():
            if r.get(source):values[key]=[r[source]]
        for key in ('check_min','check_max','currency','revenue_min','revenue_max','ebitda_min','ebitda_max'):
            if r.get(key) is not None:values[key]=r[key]
        if r.get('sectors'):values['sectors']=r['sectors']
        if values:
            # A separate mandate could broaden existing criteria unexpectedly. Fill one blank
            # strategy only; leave multiple existing strategies for follow-up.
            active=[m for m in mandates if m['active']]
            if len(active)<=1:
                m=active[0] if active else {'name':'Published website criteria','criteria':{},'sources':[],'evidence_status':'source_stated','active':True}
                changed=False
                for k,v in values.items():
                    if m['criteria'].get(k) in (None,'',[],{}):m['criteria'][k]=v;changed=True
                if changed:
                    Criteria.model_validate(m['criteria'])
                    m['sources']=list(dict.fromkeys(m['sources']+proposal['sources']))
                    if not active:mandates.append(m)
            else:notes.append('Multiple investment strategies: review researched criteria against each mandate.')
        notes += [x for x in (r.get('evidence'),r.get('uncertainty')) if x]
    return InvestorEdit(revision=profile['revision'],organization=org,contacts=contacts,mandates=mandates,research_reviewed=True,research_sources=proposal['sources'],research_summary='Automatically populated from matched public sources under Nia’s authorization. Existing fields preserved.\n'+'\n'.join(dict.fromkeys(notes)))
