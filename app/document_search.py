"""Search original SharePoint documents with the current user's delegated token."""
import re,html
from urllib.parse import urlparse
import httpx
from fastapi import HTTPException
from . import library
CATEGORIES={
 'nda':{'title':'NDAs','terms':['NDA','non disclosure','nondisclosure','confidentiality agreement']},
 'engagement':{'title':'Engagement letters & agreements','terms':['engagement letter','engagement agreement']},
 'email':{'title':'Email templates','terms':['email template','email script','outreach template','prospecting email']},
 'phone':{'title':'Phone scripts','terms':['phone script','call script','cold calling','phone pitch']},
 'wsp':{'title':'Written Supervisory Procedures','terms':['WSP','written supervisory procedures']},
 'oba':{'title':'OBA forms','terms':['OBA','outside business activities','outside business activity']},
 'process':{'title':'Xnergy process documents','terms':['Xnergy process','XnergyGPT','onboarding checklist','deal process']}}

def category_for(question):
    q=question.lower()
    for category,data in CATEGORIES.items():
        if any(re.search(r'\b'+re.escape(term.lower())+r's?\b',q) for term in data['terms']):return category
    if 'engagement' in q:return 'engagement'
    return None

def safe_terms(terms):
    cleaned=[]
    for term in terms[:5]:
        value=' '.join(re.findall(r'[\w-]+',str(term),flags=re.UNICODE))[:120].strip()
        if value and value not in cleaned:cleaned.append(value)
    if not cleaned:raise HTTPException(422,'Enter a document name or topic to search.')
    return cleaned

def query_string(terms):
    # No raw user/model KQL; only literal phrases in a fixed SharePoint scope.
    return '('+' OR '.join('"'+v+'"' for v in safe_terms(terms))+') AND isDocument:true AND path:"https://netorg14312891.sharepoint.com"'

def clean_snippet(value):
    return ' '.join(html.unescape(re.sub(r'<[^>]*>',' ',value or '')).split())[:240]

def document_cards(data):
    cards=[];seen=set();more=False
    for response in data.get('value',[]):
        for container in response.get('hitsContainers',[]):
            more=more or bool(container.get('moreResultsAvailable'))
            for hit in container.get('hits',[]):
                r=hit.get('resource',{});url=r.get('webUrl','');parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.hostname!='netorg14312891.sharepoint.com' or parsed.username or parsed.password or not r.get('name') or url in seen:continue
                seen.add(url)
                cards.append({'id':r.get('id') or hit.get('hitId'),'name':r['name'],'url':url,'modified':r.get('lastModifiedDateTime'),
                  'description':clean_snippet(hit.get('summary')),'location':(r.get('parentReference') or {}).get('path',''),'source':'SharePoint'})
    return cards,more

async def search_documents(user,terms,offset=0):
    try:
        token=await library.token(user)
        if not token:return {'connected':False,'documents':[],'has_more':False,'terms':safe_terms(terms)}
        payload={'requests':[{'entityTypes':['driveItem'],'query':{'queryString':query_string(terms)},'from':offset,'size':20}]}
        async with httpx.AsyncClient(timeout=25) as client:
            r=await client.post('https://graph.microsoft.com/v1.0/search/query',headers={'Authorization':'Bearer '+token},json=payload)
        if r.status_code==401:
            await library.security._redis().delete(library.key(user))
            return {'connected':False,'documents':[],'has_more':False,'terms':safe_terms(terms)}
        if r.status_code==403:raise HTTPException(403,'Microsoft did not allow SharePoint search. Reconnect SharePoint or ask your Microsoft administrator to approve access.')
        if r.status_code==429:raise HTTPException(429,'SharePoint search is busy. Please try again shortly.')
        r.raise_for_status();cards,more=document_cards(r.json())
        return {'connected':True,'documents':cards,'has_more':more,'terms':safe_terms(terms),'next_offset':offset+20 if more else None}
    except httpx.HTTPError:raise HTTPException(502,'SharePoint search is temporarily unavailable. Please try again.')
