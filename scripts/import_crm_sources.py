"""Import reviewed source rows from a private blob, preserving a per-row ledger.

Only explicitly classified company prospects become CRM companies here. Other
rows remain admin-only import-review candidates. This does not infer investor
status from a spreadsheet filename or grant document access.
"""
import asyncio, json, os, ssl, sys, uuid
from collections import Counter
import asyncpg
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import BlobClient

async def import_batch(c,batch,tenant,stats):
    async with c.transaction():
        await c.execute("select set_config('app.tenant_id',$1,true),set_config('app.user_role','admin',true)",str(tenant))
        existing_keys={x['source_key'] for x in await c.fetch('select source_key from crm_source_records where tenant_id=$1 and source_key=any($2::text[])',tenant,[r['source_key'] for r in batch])}
        ledger=[]
        for r in batch:
            if r['source_key'] in existing_keys:
                stats['already_recorded']+=1;continue
            n=r['normalized'];prospect=None;org=None;status=r['status']
            if n.get('crm_category')=='company_prospect' and n.get('classification_basis')=='user_confirmed_source_list' and status=='candidate' and n.get('firm'):
                # Name collisions require review; no automatic organization merge.
                existing=await c.fetchval('select id from company_prospects where lower(name)=lower($1)',n['firm'].strip())
                if existing:status='possible_duplicate'
                else:
                    website=n.get('website_url')
                    if website and not website.startswith(('https://','http://')):website=None
                    prospect=await c.fetchval('''insert into company_prospects(tenant_id,name,website_url,service_need,status)
                        values($1,$2,$3,'unknown','unqualified') returning id''',tenant,n['firm'].strip(),website)
                    stats['prospects_created']+=1;status='imported_prospect'
            elif n.get('crm_category')=='investor_buyer' and n.get('classification_basis')=='verified_research_pilot' and status=='candidate':
                existing=await c.fetchval('select id from investor_organizations where lower(name)=lower($1)',n['firm'])
                if existing:status='possible_duplicate'
                else:
                    org=await c.fetchval("insert into investor_organizations(tenant_id,name,website_url,source) values($1,$2,$3,'SharePoint research pilot') returning id",tenant,n['firm'],n.get('website_url'))
                    for fact in n.get('verified_facts',[]):
                        await c.execute('insert into investor_evidence(tenant_id,organization_id,field_name,value,source_url,status) values($1,$2,$3,$4::jsonb,$5,$6)',tenant,org,fact['field'],json.dumps(fact['value']),fact['source_url'],fact['status'])
                    stats['investors_created']+=1;status='imported_investor'
            elif status=='candidate':status='needs_classification'
            ledger.append((tenant,r['source_key'],r['filename'],r['sheet'],r['row_number'],r.get('source_url'),json.dumps(r['raw']),json.dumps(n),status,prospect,org))
        if ledger:
            await c.executemany("""insert into crm_source_records(tenant_id,source_key,filename,sheet_name,row_number,source_url,raw_record,normalized_record,status,prospect_id,organization_id)
                values($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10,$11)""",ledger)
            stats['source_rows_recorded']+=len(ledger)

async def main():
    c=await asyncpg.connect(host=os.environ['PGHOST'],database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context(),command_timeout=120)
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    blob=BlobClient(account_url=os.environ['IMPORT_ACCOUNT_URL'],container_name=os.environ['IMPORT_CONTAINER'],blob_name=os.environ['IMPORT_BLOB'],credential=credential)
    stats=Counter()
    try:
        # Exact existing Xnergy tenant; never select an arbitrary first tenant.
        tenant=await c.fetchval("select id from tenants where slug='xnergy'")
        if not tenant:raise RuntimeError('Xnergy tenant unavailable')
        await c.execute('select pg_advisory_lock(681927104)')
        stream=await blob.download_blob();buffer=b'';batch=[]
        async for chunk in stream.chunks():
            buffer+=chunk
            lines=buffer.split(b'\n');buffer=lines.pop()
            for line in lines:
                if not line:continue
                batch.append(json.loads(line))
                if len(batch)>=500:
                    await import_batch(c,batch,tenant,stats);batch=[]
                    if stats['source_rows_recorded']%10000==0:print(json.dumps(dict(stats)),flush=True)
        if buffer.strip():batch.append(json.loads(buffer))
        if batch:await import_batch(c,batch,tenant,stats)
        print('Import completed: '+json.dumps(dict(stats)),flush=True)
    finally:
        await blob.close();await credential.close();await c.close()

if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as e:
        print('Import failed: '+type(e).__name__+'. Committed batches are retained and retryable; no source values logged.',file=sys.stderr);sys.exit(1)
