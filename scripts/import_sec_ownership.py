"""Import last-three-year SEC public ownership bulk files; safe to resume."""
import asyncio,json,os,sys
sys.path.insert(0,'/app')
import httpx
from app import db
from app.sec_ownership import parse_quarter,save_batch
from app.sec_industries import enrich_holding_sectors
from app.sec_recent import import_recent

BASE='https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/'
QUARTERS=[f'{y}q{q}' for y in range(2023,2027) for q in range(1,5) if (y,q)>=(2023,3) and (y,q)<=(2026,2)]

async def main():
    await db.init_db()
    base={'tenant_id':os.environ['RESEARCH_TENANT_ID'],'user_id':None,'role':'admin'}
    try:
      async with db.pool().acquire() as guard:
        if not await guard.fetchval('select pg_try_advisory_lock(71922028)'):return
        try:
          async with db.tenant_tx(base) as c:
            owner=await c.fetchrow("select id,role from users where email='nia@xnergy.com' and role='admin' and active")
            if not owner:raise RuntimeError('Authorized administrator not found')
          u={**base,'user_id':str(owner['id'])}
          async with httpx.AsyncClient(timeout=120,headers={'User-Agent':os.environ.get('SEC_USER_AGENT','Xnergy-DealOS-Public-Data/1.0')}) as client:
            for quarter in reversed(QUARTERS):
              key='sec:quarter:'+quarter+':20230922-20260922'
              async with db.tenant_tx(u) as c:
                if await c.fetchval("select id from crm_source_records where source_key=$1 and status='sec_import_complete'",key):continue
              source=BASE+quarter+'_form345.zip'
              if quarter=='2026q2':source=source.replace('/structureddata/','/datastandardsinnovation/')
              response=await client.get(source);response.raise_for_status()
              if len(response.content)>50*1024*1024:raise ValueError('SEC download exceeds size bound')
              batch=[];filings=0;created=0
              for record in parse_quarter(response.content,source):
                batch.append(record)
                if len(batch)>=1000:
                  async with db.tenant_tx(u) as c:created+=await save_batch(c,u,batch)
                  filings+=len(batch);batch=[]
              if batch:
                async with db.tenant_tx(u) as c:created+=await save_batch(c,u,batch)
                filings+=len(batch)
              async with db.tenant_tx(u) as c:
                await c.execute("insert into crm_source_records(tenant_id,source_key,filename,source_url,raw_record,status) values($1::uuid,$2,$3,$4,$5::jsonb,'sec_import_complete') on conflict(tenant_id,source_key) do nothing",u['tenant_id'],key,source.rsplit('/',1)[-1],source,json.dumps({'source':'SEC','quarter':quarter,'filing_owner_rows':filings,'new_investors':created,'start':'2023-09-22','end':'2026-09-22'}))
              print(json.dumps({'quarter':quarter,'filing_owner_rows':filings,'new_investors':created,'status':'imported'}),flush=True)
              await asyncio.sleep(1)
            await enrich_holding_sectors(client,u)
            await import_recent(client,u)
            await enrich_holding_sectors(client,u)
        finally:await guard.execute('select pg_advisory_unlock(71922028)')
    finally:await db.close_db()

asyncio.run(main())
