"""Complete the current quarter from the SEC public filing index, with checkpoints."""
import asyncio,re,json
from datetime import date
from xml.etree import ElementTree as ET
from .sec_ownership import START,END,FORMS,save_batch
from .db import tenant_tx

def index_filings(text):
    result={}
    for line in text.splitlines():
        parts=line.split('|')
        if len(parts)!=5 or parts[2] not in FORMS:continue
        try:day=date.fromisoformat(parts[3])
        except ValueError:continue
        if not START<=day<=END:continue
        path=parts[4]
        if not re.fullmatch(r'edgar/data/\d+/\d{10}-\d{2}-\d{6}\.txt',path):continue
        result[path.rsplit('/',1)[-1][:-4]]=path
    return result

def parse_filing(raw,acc,path):
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('Unsupported XML declaration')
    m=re.search(rb'<ownershipDocument\b[^>]*>.*?</ownershipDocument>',raw,re.S)
    if not m:raise ValueError('Ownership XML missing')
    root=ET.fromstring(m.group())
    def text(node,p):return (node.findtext(p) or '').strip()
    form=text(root,'documentType')
    if form not in FORMS:raise ValueError('Not an ownership filing')
    issuer=text(root,'issuer/issuerCik')
    if not issuer.isdigit():raise ValueError('Issuer identifier missing')
    issuer=str(int(issuer));filed=re.search(rb'FILED AS OF DATE:\s*(\d{8})',raw)
    if not filed:raise ValueError('Filing date missing')
    d=filed.group(1).decode();day=date(int(d[:4]),int(d[4:6]),int(d[6:8]))
    if not START<=day<=END:return []
    rows=[]
    for owner in root.findall('reportingOwner'):
        cik=text(owner,'reportingOwnerId/rptOwnerCik');name=text(owner,'reportingOwnerId/rptOwnerName')
        if not cik.isdigit() or not name:continue
        roles=[label for field,label in [('isDirector','Director'),('isOfficer','Officer'),('isTenPercentOwner','10% Owner'),('isOther','Other')] if text(owner,'reportingOwnerRelationship/'+field).lower() in ('1','true')]
        rows.append({'cik':str(int(cik)),'name':name,'role':', '.join(roles),'title':text(owner,'reportingOwnerRelationship/officerTitle'),'accession':acc,'form':form,'filing_date':day.strftime('%d-%b-%Y').upper(),'issuer_cik':issuer,'issuer_name':text(root,'issuer/issuerName'),'issuer_symbol':text(root,'issuer/issuerTradingSymbol'),'source':'SEC','source_file':path.rsplit('/',1)[-1],'source_url':'https://www.sec.gov/Archives/'+path,'filing_url':f'https://www.sec.gov/Archives/edgar/data/{issuer}/{acc.replace("-","")}/{acc}-index.htm'})
    return rows

async def import_recent(client,user):
    url='https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/master.idx'
    response=await client.get(url);response.raise_for_status()
    files=index_filings(response.text)
    async with tenant_tx(user) as c:
        done={r['source_key'].removeprefix('sec:recent:') for r in await c.fetch("select source_key from crm_source_records where source_key like 'sec:recent:%' and status='sec_import_complete'")}
    pending=[(a,p) for a,p in files.items() if a not in done]
    print(json.dumps({'current_quarter_filings':len(files),'pending':len(pending)}),flush=True)
    # Sequential requests plus delay remain below the SEC fair-access request limit.
    for number,(acc,path) in enumerate(pending,1):
        source='https://www.sec.gov/Archives/'+path
        try:
            response=await client.get(source)
            if response.status_code in (403,429):raise RuntimeError('SEC requests paused: HTTP '+str(response.status_code))
            response.raise_for_status()
            if len(response.content)>5*1024*1024:raise ValueError('Filing too large')
            rows=parse_filing(response.content,acc,path)
            async with tenant_tx(user) as c:
                if rows:await save_batch(c,user,rows)
                await c.execute("insert into crm_source_records(tenant_id,source_key,filename,source_url,raw_record,status) values($1::uuid,$2,$3,$4,$5::jsonb,'sec_import_complete') on conflict(tenant_id,source_key) do update set status='sec_import_complete',raw_record=excluded.raw_record",user['tenant_id'],'sec:recent:'+acc,path.rsplit('/',1)[-1],source,json.dumps({'source':'SEC','accession':acc,'reporting_owners':len(rows)}))
        except RuntimeError:raise
        except Exception as error:
            async with tenant_tx(user) as c:
                await c.execute("insert into crm_source_records(tenant_id,source_key,filename,source_url,raw_record,status) values($1::uuid,$2,$3,$4,$5::jsonb,'sec_import_failed') on conflict(tenant_id,source_key) do update set status='sec_import_failed',raw_record=excluded.raw_record",user['tenant_id'],'sec:recent:'+acc,path.rsplit('/',1)[-1],source,json.dumps({'source':'SEC','error':type(error).__name__}))
        if number%100==0:print(json.dumps({'current_quarter_processed':number,'total':len(pending)}),flush=True)
        await asyncio.sleep(.4)
