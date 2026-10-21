"""Public SEC identity evidence. No inference of check sizes from assets or filings."""
import json,re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

DATA=Path(__file__).with_name('data')/'sec_advisers.json'
def name_key(value):
    words=re.sub(r'[^a-z0-9 ]',' ',str(value or '').lower()).split()
    return ''.join(w for w in words if w not in {'llc','llp','inc','ltd','limited','the','corp','corporation'})
def website(value):
    value=str(value or '').strip()
    if not value:return None
    if not re.match(r'^https?://',value,re.I):value='https://'+value
    try:
        u=urlsplit(value)
        if u.scheme.lower() not in ('http','https') or not u.hostname or u.username or u.password or any(c.isspace() for c in value):return None
        return u.scheme.lower()+'://'+u.netloc.lower()+u.path
    except ValueError:return None

def host(value):
    return (urlsplit(website(value) or '').hostname or '').removeprefix('www.')
@lru_cache(maxsize=1)
def directory():
    if not DATA.exists():return {'as_of':None,'records':[]},{}
    data=json.loads(DATA.read_text());index={}
    for r in data['records']:
        for n in {name_key(n) for n in r['names']}:
            if n:index.setdefault(n,[]).append(r)
    return data,index

def sec_match(profile):
    org=profile['organization'];_,index=directory()
    candidates={r['crd']:r for r in index.get(name_key(org['name']),[])}
    if len(candidates)!=1:return None
    row=next(iter(candidates.values()))
    # An explicit conflicting website means the otherwise equal names are insufficient.
    if org.get('website_url') and row.get('website_url') and host(org['website_url'])!=host(row['website_url']):return None
    return row

def sec_record(row,name):
    fields={k:row.get(k) or None for k in ('firm_phone','street_address','city','state_region','postal_code','country')}
    return dict(fields,category='investor_buyer',firm_name=name,evidence='SEC Form ADV adviser directory; CRD '+row['crd']+'; filing date '+str(row.get('filing_date') or 'not supplied')+'. Firm contact details only; no investment mandate inferred.')

def coverage():
    data,_=directory()
    return [
        {'name':'SEC Form ADV','status':'Connected' if data['records'] else 'Unavailable','detail':str(len(data['records']))+' public adviser records; snapshot '+str(data['as_of'])},
        {'name':'VC and PE firm websites','status':'Connected','detail':'Published strategy, criteria, sector, stage, team and contact pages on identified firm websites.'},
        {'name':'Apollo','status':'Not connected','detail':'Requires access to Nia’s company workspace and its API credentials.'},
        {'name':'PitchBook','status':'Not connected','detail':'Requires a PitchBook API agreement and credentials.'},
        {'name':'ACG member directory','status':'Not connected','detail':'Requires authorized directory access. Authorized CSV/XLSX exports can be uploaded.'}
    ]
