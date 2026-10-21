"""Explicit public projection. Private CRM rows are never serialized wholesale."""
import hashlib,json,re
from urllib.parse import urlsplit

CLIENT_STAGES={'engaged_client','signed_client','signed','placement','transaction','closing','completed'}
STAGES={'initial_identification','contact_made','vetting','engagement_sent','engaged_client','completed','archived_historical','inactive'}
CRITERIA={'specialty_tags','franchise_models','sectors','geographies','financing_types','stages','transaction_types','ownership_preferences','excluded_sectors','excluded_geographies','currency','check_min','check_max','revenue_min','revenue_max','ebitda_min','ebitda_max'}
def obj(v):
    if isinstance(v,str):
        try:return json.loads(v)
        except (TypeError,ValueError):return {}
    return dict(v or {})
def norm(v):return re.sub(r'[^a-z0-9]','',str(v or '').lower())
def host(v):
    try:return (urlsplit(v if '://' in (v or '') else 'https://'+(v or '')).hostname or '').lower().removeprefix('www.')
    except ValueError:return ''
def ident(kind,v):return kind+'-'+hashlib.sha256(str(v).encode()).hexdigest()[:20]
def letter(i):
    out=''
    while i>=0:out=chr(65+i%26)+out;i=i//26-1
    return out

def project(investors,contacts,mandates,prospects,deals,prospect_contacts,bankers=(),teams=()):
    # All signed/completed client identities are protected, including alternate records.
    private_p={str(d.get('prospect_id')) for d in deals if d.get('stage') in CLIENT_STAGES}
    private_p.update(str(p['id']) for p in prospects if p.get('status')=='client')
    private_names={norm(d['name']) for d in deals if d.get('stage') in CLIENT_STAGES}
    private_names.update(norm(p['name']) for p in prospects if str(p['id']) in private_p)
    private_names.discard('')
    private_domains={host(p.get('website_url')) for p in prospects if str(p['id']) in private_p or norm(p['name']) in private_names};private_domains.discard('')
    # Match domain aliases as well as name aliases before emitting anything.
    for p in prospects:
        if norm(p['name']) in private_names or host(p.get('website_url')) in private_domains:private_p.add(str(p['id']))
    private_names.update(norm(p['name']) for p in prospects if str(p['id']) in private_p)
    forbidden=set(private_names)|{norm(v) for v in private_domains}
    for c in prospect_contacts:
        if str(c.get('prospect_id')) in private_p:
            forbidden.update(norm(c.get(k)) for k in ('name','email','phone'))
    forbidden.discard('')
    pattern=re.compile('|'.join(re.escape(t) for t in sorted(forbidden,key=len,reverse=True))) if forbidden else None
    def secret(v):return bool(pattern and pattern.search(norm(v)))
    def clean(v):
        if v is None:return None
        if isinstance(v,(int,float,bool)):return v
        if isinstance(v,list):return [clean(x) for x in v if not secret(x)]
        if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
        text=str(v)
        return None if secret(text) else text
    def url(v):
        v=clean(v)
        if not v:return None
        try:
            u=urlsplit(v)
            if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return None
            return u.scheme+'://'+u.netloc+u.path  # no auth or query strings
        except ValueError:return None
    def is_private(r):return str(r.get('id')) in private_p or norm(r.get('name')) in private_names or host(r.get('website_url') or r.get('domain')) in private_domains or secret(r.get('name'))
    banker_map={str(b['id']):{'id':ident('b',b['id']),'name':clean(b['display_name'])} for b in bankers}
    team_map={(t['entity_kind'],str(t['entity_id'])):t for t in teams}
    def team(kind,r):
        t=team_map.get((kind,str(r['id'])),{})
        ids=list(t.get('banker_ids') or [])
        if r.get('owner_user_id'):ids.append(r['owner_user_id'])
        people=[banker_map[str(v)] for v in dict.fromkeys(ids) if str(v) in banker_map]
        manager=banker_map.get(str(t.get('manager_user_id')))
        return {'bankers':people,'manager':manager}
    by_contacts={};by_mandates={} 
    for c in contacts:
        if any(secret(c.get(k)) for k in ('name','email','phone')):continue
        by_contacts.setdefault(str(c['organization_id']),[]).append({k:clean(c.get(k)) for k in ('name','title','email','phone')})
    for m in mandates:
        criteria=obj(m.get('criteria'))
        by_mandates.setdefault(str(m['organization_id']),[]).append({'evidence_status':clean(m.get('evidence_status')),'criteria':{k:clean(v) for k,v in criteria.items() if k in CRITERIA}})
    ir=[]
    for r in investors:
        if is_private(r):continue
        ir.append({'id':ident('i',r['id']),'name':clean(r['name']),'website':url(r.get('website_url') or ('https://'+r['domain'] if r.get('domain') else None)),
          'sector':clean(r.get('sector_focus')),'holding_sectors':clean(r.get('observed_holding_sectors')),'geography':clean(r.get('geography')),'category':clean(r.get('firm_type')),
          'headquarters':clean(r.get('headquarters')),'entity_kind':clean(r.get('entity_kind')),'sec_cik':clean(r.get('sec_cik')),
          'description':clean(r.get('description')),'notes':clean(r.get('notes')),**team('investor',r),'phone':clean(r.get('phone')),'email':clean(r.get('email')),'contacts':by_contacts.get(str(r['id']),[]),'mandates':by_mandates.get(str(r['id']),[])})
    # Public clients have no original ID, sector, finances, website, contact or timeline.
    client_keys={}
    def private_key(p=None,d=None):
        if p:return 'p:'+str(p['id'])
        return 'p:'+str(d['prospect_id']) if d.get('prospect_id') else 'n:'+norm(d['name'])
    for p in prospects:
        if is_private(p):client_keys[private_key(p=p)]=None
    for d in deals:
        if d.get('stage') in CLIENT_STAGES or str(d.get('prospect_id')) in private_p or secret(d.get('name')):client_keys[private_key(d=d)]=None
    for i,k in enumerate(sorted(client_keys)):client_keys[k]={'id':'private-'+str(i+1),'name':'Private Client '+letter(i),'private':True,'stage':'engaged_client'}
    pr=[];pmap={}
    for p in prospects:
        if is_private(p):pmap[str(p['id'])]=client_keys[private_key(p=p)];continue
        prof=obj(p.get('company_profile'))
        r={'id':ident('p',p['id']),'name':clean(p['name']),'website':url(p.get('website_url')),'sector':clean(p.get('sector')),'geography':clean(p.get('geography')),
           'notes':clean(p.get('notes')),**team('prospect',p),'contacts':[{k:clean(c.get(k)) for k in ('name','title','email','phone')} for c in prospect_contacts if str(c['prospect_id'])==str(p['id']) and not any(secret(c.get(k)) for k in ('name','email','phone'))],'service':clean(p.get('service_need')),'revenue':prof.get('annual_revenue') if isinstance(prof.get('annual_revenue'),(int,float)) else None,
           'added':str(p.get('created_at') or '')[:10],'stage':{'contacted':'contact_made','researching':'vetting','qualified':'vetting','not_a_fit':'inactive'}.get(p.get('status'),'initial_identification')}
        if secret(json.dumps(r,default=str)):continue
        pr.append(r);pmap[str(p['id'])]=r
    dr=[];seen=set()
    for d in deals:
        key=private_key(d=d)
        if key in client_keys:
            r=dict(client_keys[key])
            if r['id'] in seen:continue
        else:
            r=dict(pmap.get(str(d.get('prospect_id'))) or {'id':ident('d',d['id']),'name':clean(d.get('name'))})
            r.update(**team('deal',d))
            r.update(stage=d['stage'] if d.get('stage') in STAGES else 'initial_identification',service=clean(d.get('mandate_type')),added=str(d.get('created_at') or '')[:10])
            if not r.get('name') or secret(json.dumps(r,default=str)):continue
        seen.add(r['id']);dr.append(r)
    for r in client_keys.values():
        if r['id'] not in seen:dr.append(dict(r));seen.add(r['id'])
    pr.extend(dict(r) for r in client_keys.values())
    return {'investors':ir,'prospects':pr,'deals':dr,'private_clients':len(client_keys),'bankers':list(banker_map.values())}
