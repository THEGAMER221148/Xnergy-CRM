"""Exercise proposed CRM schema and tenant isolation, then roll back every change."""
import asyncio, os, ssl, sys, uuid
from pathlib import Path
import asyncpg

async def main():
    c=await asyncpg.connect(host=os.environ['PGHOST'],database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context())
    transaction=c.transaction()
    await transaction.start()
    try:
        await c.execute('select pg_advisory_xact_lock(681927103)')
        for name in ('004_investor_crm.sql','005_company_prospects.sql','006_prospect_contacts.sql','007_import_review.sql','008_referral_sources.sql'):
            if not await c.fetchval('select exists(select 1 from dealos_schema_migrations where version=$1)',name.removesuffix('.sql')):
                await c.execute((Path(__file__).resolve().parents[1]/'sql'/name).read_text())
        tenant,other,user,otheruser,org,prospect=[uuid.uuid4() for _ in range(6)]
        await c.execute('insert into tenants(id,name,slug) values($1,$2,$3),($4,$5,$6)',tenant,'CRM test','crm-test-'+str(tenant),other,'Other test','crm-test-'+str(other))
        await c.execute("select set_config('app.tenant_id',$1,true),set_config('app.user_role','admin',true)",str(tenant))
        await c.execute("insert into users(id,tenant_id,email,display_name,role) values($1,$2,'test@example.invalid','Test','banker')",user,tenant)
        await c.execute("select set_config('app.tenant_id',$1,true)",str(other))
        await c.execute("insert into users(id,tenant_id,email,display_name,role) values($1,$2,'test@example.invalid','Test','banker')",otheruser,other)
        await c.execute("select set_config('app.tenant_id',$1,true),set_config('app.user_id',$2,true),set_config('app.user_role','banker',true)",str(tenant),str(user))
        await c.execute('set local role dealos_app')
        await c.execute("insert into investor_organizations(id,tenant_id,name,website_url) values($1,$2,'Test investor','https://example.invalid')",org,tenant)
        await c.execute("insert into company_prospects(id,tenant_id,name,owner_user_id) values($1,$2,'Test company',$3)",prospect,tenant,user)
        await c.execute("insert into investor_mandates(tenant_id,organization_id,evidence_status) values($1,$2,'unverified')",tenant,org)
        await c.execute("insert into crm_tasks(tenant_id,prospect_id,assigned_to,title) values($1,$2,$3,'Test follow-up')",tenant,prospect,user)
        assert await c.fetchval('select count(*) from company_prospects where id=$1',prospect)==1
        try:
            async with c.transaction():
                await c.execute('update company_prospects set owner_user_id=$1 where id=$2',otheruser,prospect)
        except asyncpg.ForeignKeyViolationError: pass
        else: raise RuntimeError('Cross-tenant owner was accepted')
        await c.execute("select set_config('app.tenant_id',$1,true)",str(other))
        assert await c.fetchval('select count(*) from company_prospects where id=$1',prospect)==0
        await c.execute("select set_config('app.tenant_id',$1,true),set_config('app.user_role','investor',true)",str(tenant))
        for table in ('company_prospects','investor_mandates','crm_tasks'):
            assert await c.fetchval('select count(*) from '+table)==0
        print('PASS: CRM schema, linked records, cross-tenant assignment rejection, tenant isolation, and external-investor restrictions.')
    finally:
        await transaction.rollback()
        await c.close()
        print('Test transaction rolled back; no test records or schema changes retained.')

if __name__=='__main__':
    try: asyncio.run(main())
    except Exception as error:
        print('CRM verification failed: '+type(error).__name__+'. No credentials logged.',file=sys.stderr)
        sys.exit(1)
