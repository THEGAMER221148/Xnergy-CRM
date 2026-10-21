"""SEC Forms 3/4/5 source-preserving import. Holdings are not investment mandates."""
import csv,io,json,zipfile,re
from datetime import datetime,date
from uuid import UUID,uuid5

FORMS={'3','3/A','4','4/A','5','5/A'}
START=date(2023,9,22)
END=date(2026,9,22)

def parse_quarter(raw,source):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>600*1024*1024:raise ValueError('SEC archive exceeds allowed expanded size')
        def rows(name):return csv.DictReader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig'),delimiter='\t')
        submissions={}
        for r in rows('SUBMISSION.tsv'):
            filed=datetime.strptime(r['FILING_DATE'],'%d-%b-%Y').date()
            if START<=filed<=END and r['DOCUMENT_TYPE'] in FORMS:submissions[r['ACCESSION_NUMBER']]=r
        for r in rows('REPORTINGOWNER.tsv'):
            sub=submissions.get(r['ACCESSION_NUMBER'])
            if not sub or not r['RPTOWNERCIK'].isdigit() or not r['RPTOWNERNAME'].strip():continue
            cik=str(int(r['RPTOWNERCIK']));acc=r['ACCESSION_NUMBER'];issuer=str(int(sub['ISSUERCIK']))
            yield {'cik':cik,'name':r['RPTOWNERNAME'].strip(),'role':r['RPTOWNER_RELATIONSHIP'],'title':r['RPTOWNER_TITLE'],'accession':acc,'form':sub['DOCUMENT_TYPE'],'filing_date':sub['FILING_DATE'],'issuer_cik':issuer,'issuer_name':sub['ISSUERNAME'],'issuer_symbol':sub['ISSUERTRADINGSYMBOL'],'source':'SEC','source_file':source.rsplit('/',1)[-1],'source_url':source,'filing_url':f'https://www.sec.gov/Archives/edgar/data/{issuer}/{acc.replace("-","")}/{acc}-index.htm'}

async def save_batch(c,user,records):
    """Tenant-scoped and idempotent by reporting-owner CIK and filing accession."""
    tenant=UUID(str(user['tenant_id']));owners={}
    for r in records:
        r['organization_id']=str(uuid5(tenant,'sec-reporting-owner:'+r['cik']))
        name=r['name']
        r['entity_kind']='Company' if re.search(r'\b(LLC|LTD|INC|CORP|CORPORATION|LP|LLP|TRUST|FUND|PARTNERS|HOLDINGS)\b',name,re.I) else 'Individual' if ('Officer' in r['role'] or 'Director' in r['role']) else 'Unclassified'
        r['first_name']=None;r['last_name']=None
        if r['entity_kind']=='Individual' and name.count(',')==1:
            last,first=[x.strip() for x in name.split(',')]
            if first and last and first.upper() not in ('JR','SR','JR.','SR.','II','III','IV'):r['first_name']=first;r['last_name']=last
        old=owners.get(r['cik'])
        if old is None or datetime.strptime(r['filing_date'],'%d-%b-%Y')>datetime.strptime(old['filing_date'],'%d-%b-%Y'):owners[r['cik']]=r
    owner_json=json.dumps(list(owners.values()));filing_json=json.dumps(records)
    created=await c.execute('''insert into investor_organizations(tenant_id,id,name,firm_type,description,notes,relationship_owner,discovery_status,enrichment_status,source,entity_kind,sec_cik)
      select $1::uuid,(x->>'organization_id')::uuid,x->>'name','SEC Insider',
      'SEC reporting owner. Reported relationship: '||(x->>'role')||' at '||(x->>'issuer_name')||'. Investment mandate, sector, stage and check size are not established by this filing.',
      'Source: SEC | File: '||(x->>'source_file')||' | Form: '||(x->>'form')||' | Filed: '||(x->>'filing_date')||' | CIK: '||(x->>'cik')||' | '||(x->>'filing_url'),
      'Nia Stefani','sec_form_345','needs_follow_up','SEC',x->>'entity_kind',x->>'cik'
      from jsonb_array_elements($2::jsonb) x on conflict(id) do nothing''',tenant,owner_json)
    await c.execute('''insert into investor_contacts(id,tenant_id,organization_id,name,first_name,last_name,title)
      select (x->>'organization_id')::uuid,$1::uuid,(x->>'organization_id')::uuid,x->>'name',x->>'first_name',x->>'last_name',x->>'title'
      from jsonb_array_elements($2::jsonb) x where x->>'entity_kind'='Individual' on conflict(id) do nothing''',tenant,owner_json)
    for prefix,data,source_expr in [('sec:owner:',owner_json,"x->>'cik'"),('sec:filing:',filing_json,"(x->>'accession')||':'||(x->>'cik')")]:
        await c.execute('''insert into crm_source_records(tenant_id,organization_id,source_key,filename,source_url,raw_record,normalized_record,status)
          select $1::uuid,(x->>'organization_id')::uuid,$3::text||('''+source_expr+'''),x->>'source_file',x->>'filing_url',x,x,'imported_investor'
          from jsonb_array_elements($2::jsonb) x on conflict(tenant_id,source_key) do nothing''',tenant,data,prefix)
    await c.execute('''insert into crm_tasks(tenant_id,organization_id,assigned_to,title,task_kind)
      select $1::uuid,(x->>'organization_id')::uuid,$3::uuid,'Qualify SEC investor and find business contact: '||(x->>'name'),'research'
      from jsonb_array_elements($2::jsonb) x where not exists(select 1 from crm_tasks t where t.organization_id=(x->>'organization_id')::uuid)''',tenant,owner_json,user['user_id'])
    return int(created.split()[-1])
