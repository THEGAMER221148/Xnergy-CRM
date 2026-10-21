"""Search semantics shared by the approved investor workspace; no inferred facts."""
from decimal import Decimal
from pydantic import Field
from .crm_models import Record

class SearchSelections(Record):
    category:list[str]=Field(default_factory=list,max_length=30)
    transaction:list[str]=Field(default_factory=list,max_length=30)
    type:list[str]=Field(default_factory=list,max_length=30)
    ownership:list[str]=Field(default_factory=list,max_length=30)
    sector:list[str]=Field(default_factory=list,max_length=100)
    subsector:list[str]=Field(default_factory=list,max_length=100)
    stage:list[str]=Field(default_factory=list,max_length=30)
    geo:list[str]=Field(default_factory=list,max_length=100)
    hq:list[str]=Field(default_factory=list,max_length=100)
    tags:list[str]=Field(default_factory=list,max_length=100)
    franchise:list[str]=Field(default_factory=list,max_length=30)

class InvestorSearch(Record):
    holding_sector:str=Field(default="",max_length=300)
    firm:str=Field(default='',max_length=300)
    contact:str=Field(default='',max_length=300)
    email:str=Field(default='',max_length=320)
    phone:str=Field(default='',max_length=100)
    banker:str=Field(default='',max_length=300)
    minimum:Decimal|None=Field(default=None,ge=0)
    maximum:Decimal|None=Field(default=None,ge=0)
    revenue:Decimal|None=Field(default=None,ge=0)
    ebitda:Decimal|None=None
    currency:str=Field(default='USD',pattern='^[A-Z]{3}$')
    unknown:bool=False
    selections:SearchSelections=Field(default_factory=SearchSelections)
    offset:int=Field(default=0,ge=0)
    limit:int=Field(default=50,ge=1,le=100)

ALIASES={'venture capital':'vc fund','angel investor':'angel','private credit / lender':'private credit','corporate venture capital':'corporate vc'}
def normalize(x):
    x=str(x).strip().casefold()
    return ALIASES.get(x,x)

def match_criteria(org,criteria,s):
    missing=[];reasons=[]
    mapping={'category':[org.get('firm_type')] if org.get('firm_type') else [],'transaction':criteria.get('transaction_types',[]),'type':criteria.get('financing_types',[]),'ownership':criteria.get('ownership_preferences',[]),'sector':criteria.get('sectors',[]),'subsector':criteria.get('sectors',[]),'stage':criteria.get('stages',[]),'geo':criteria.get('geographies',[]),'hq':[org.get('headquarters')] if org.get('headquarters') else [],'tags':criteria.get('specialty_tags',[]),'franchise':criteria.get('franchise_models',[])}
    for key,wanted in s.selections.model_dump().items():
        if not wanted:continue
        actual=[normalize(v) for v in mapping[key]];wanted=[normalize(v) for v in wanted]
        if key in ('sector','subsector'):
            held=[normalize(x) for x in (org.get('observed_holding_sectors') or '').split(';') if x.strip()]
            mandate_matches=any(a==v or a.startswith(v+' / ') for a in actual for v in wanted)
            held_matches=[v for v in wanted if any(a==v or a.startswith(v+' / ') for a in held)]
            if held_matches and not mandate_matches:
                missing.append(', '.join(held_matches)+' holding — investment focus unconfirmed');continue
        if not actual:missing.append(key+' criteria missing');continue
        if key in ('sector','subsector'):
            if any(a==v or a.startswith(v+' / ') for a in actual for v in wanted):reasons.append('Sector mandate matches')
            elif any(v.startswith(a+' / ') for a in actual for v in wanted):missing.append('Subcategory preference unconfirmed')
            else:return None
        elif any(v in actual for v in wanted) or (key=='geo' and 'global' in actual) or (key=='ownership' and 'either' in actual):reasons.append(key+' matches')
        else:return None
    # Explicit exclusions override inclusions.
    for key,ex in [('sector','excluded_sectors'),('subsector','excluded_sectors'),('geo','excluded_geographies')]:
        if any(normalize(v)==normalize(a) or normalize(v).startswith(normalize(a)+' / ') for v in getattr(s.selections,key) for a in criteria.get(ex,[])):return None
    for name,low,high in [('check',s.minimum,s.maximum),('revenue',s.revenue,s.revenue),('ebitda',s.ebitda,s.ebitda)]:
        if low is None and high is None:continue
        if criteria.get('currency')!=s.currency:missing.append(name+': currency unconfirmed');continue
        a=criteria.get(name+'_min');b=criteria.get(name+'_max')
        if a is None and b is None:missing.append(name+' range missing');continue
        if low is not None and b is not None and low>Decimal(str(b)):return None
        if high is not None and a is not None and high<Decimal(str(a)):return None
        if a is None or b is None:missing.append(name+' range partly missing')
        else:reasons.append(name+' range matches')
    if missing and not s.unknown:return None
    return {'missing':missing,'reasons':reasons}
