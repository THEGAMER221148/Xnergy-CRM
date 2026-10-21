import asyncio,os,sys
sys.path.insert(0,'/app')
from app import db
from app.sec_ownership import save_batch
class Rollback(Exception):pass
async def main():
 await db.init_db()
 u={'tenant_id':os.environ['RESEARCH_TENANT_ID'],'user_id':None,'role':'admin'}
 try:
  try:
   async with db.tenant_tx(u) as c:
    owner=await c.fetchrow("select id from users where email='nia@xnergy.com' and active and role='admin'")
    u['user_id']=str(owner['id'])
    r={'cik':'9999999999','name':'SEC Validation, Example','role':'Officer','title':'CFO','accession':'9999999999-26-999999','form':'4','filing_date':'20-SEP-2026','issuer_cik':'12345','issuer_name':'Validation Issuer','issuer_symbol':'TEST','source':'SEC','source_file':'validation-test','source_url':'https://www.sec.gov/validation','filing_url':'https://www.sec.gov/validation'}
    assert not await c.fetchval("select id from investor_organizations where sec_cik='9999999999'")
    assert await save_batch(c,u,[r])==1
    assert await save_batch(c,u,[r])==0
    org=await c.fetchrow("select * from investor_organizations where sec_cik='9999999999'")
    assert org['source']=='SEC' and org['entity_kind']=='Individual'
    ct=await c.fetchrow('select * from investor_contacts where organization_id=$1',org['id'])
    assert ct['first_name']=='Example' and ct['last_name']=='SEC Validation'
    assert ct['email'] is None and ct['phone'] is None
    assert await c.fetchval('select count(*) from crm_source_records where organization_id=$1',org['id'])==2
    assert await c.fetchval('select count(*) from crm_tasks where organization_id=$1',org['id'])==1
    raise Rollback()
  except Rollback:print('SEC import transaction: mapping, source, idempotency and task checks passed; test rolled back.',flush=True)
 finally:await db.close_db()
asyncio.run(main())
