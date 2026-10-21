"""Durable website research worker. Runs under app DB role; processes approved queue only."""
import asyncio,json,os,sys
sys.path.insert(0,'/app')
from uuid import UUID
from fastapi import HTTPException
from app import db,security
from app.crm import investor_edit_snapshot,persist_investor_editor
from app.investor_research import research_profile,automatic_edit

async def main():
    await db.init_db();await security.init_security()
    tenant=os.environ['RESEARCH_TENANT_ID']
    base={'tenant_id':tenant,'user_id':None,'role':'admin'}
    try:
        # A session advisory lock prevents overlap between scheduled executions.
        async with db.pool().acquire() as guard:
            if not await guard.fetchval('select pg_try_advisory_lock(71922027)'):return
            try:
                async with db.tenant_tx(base) as c:
                    if os.environ.get('QUEUE_ALL')=='1':
                        owner=await c.fetchrow("select id from users where email='nia@xnergy.com' and role='admin' and active")
                        if not owner:raise RuntimeError('Authorized requesting admin not found')
                        result=await c.execute('insert into investor_research_jobs(tenant_id,organization_id,requested_by) select tenant_id,id,$1 from investor_organizations on conflict(tenant_id,organization_id) do nothing',owner['id'])
                        print('Queued '+result,flush=True)
                    await c.execute("update investor_research_jobs set status='pending' where status='running' and started_at<now()-interval '30 minutes'")
                for _ in range(int(os.environ.get('RESEARCH_BATCH_SIZE','20'))):
                    async with db.tenant_tx(base) as c:
                        job=await c.fetchrow("select j.*,u.role,u.active from investor_research_jobs j join users u on u.id=j.requested_by where j.status='pending' and not j.paused order by j.priority desc,j.attempts,j.created_at,j.id limit 1 for update of j skip locked")
                        if not job:break
                        if not job['active'] or job['role'] not in ('admin','banker','deal_manager'):
                            await c.execute("update investor_research_jobs set status='failed',result_summary='Requesting admin is no longer active',finished_at=now() where id=$1",job['id']);continue
                        await c.execute("update investor_research_jobs set status='running',started_at=now(),attempts=attempts+1 where id=$1",job['id'])
                    u={**base,'user_id':str(job['requested_by']),'role':job['role']}
                    proposal=None;changes=None;profile=None
                    try:
                        async with db.tenant_tx(u) as c:profile=await investor_edit_snapshot(c,job['organization_id'])
                        proposal=await research_profile(profile,None,u,background=True)
                        edit=automatic_edit(profile,proposal)
                        saved=await persist_investor_editor(job['organization_id'],edit,u)
                        async with db.tenant_tx(u) as c:after=await investor_edit_snapshot(c,job['organization_id'])
                        changes={'organization':{k:{'before':profile['organization'].get(k),'after':v} for k,v in after['organization'].items() if v!=profile['organization'].get(k) and k not in ('updated_at','notes')},'contacts_before':len(profile['contacts']),'contacts_after':len(after['contacts']),'criteria_groups_before':len(profile['mandates']),'criteria_groups_after':len(after['mandates'])}
                        status='needs_follow_up' if saved['organization']['enrichment_status']=='needs_follow_up' else 'completed'
                        summary='Supported missing fields populated; existing values preserved.'
                    except HTTPException as error:
                        if error.status_code == 502 and job['attempts'] < 2:
                            async with db.tenant_tx(u) as c:await c.execute("update investor_research_jobs set status='pending',result_summary=$2 where id=$1",job['id'],str(error.detail))
                            print(json.dumps({'job':str(job['id']),'status':'retry_pending','attempt':job['attempts']+1}),flush=True)
                            await asyncio.sleep(11)
                            continue
                        if error.status_code in (429,503):
                            async with db.tenant_tx(u) as c:await c.execute("update investor_research_jobs set status='pending',result_summary=$2 where id=$1",job['id'],str(error.detail))
                            print('Paused for service limit: '+str(error.status_code),flush=True);break
                        status='needs_follow_up';summary=str(error.detail)
                    except ValueError as error:status='needs_follow_up';summary=str(error)
                    except Exception as error:
                        status='failed';summary='Research failed: '+type(error).__name__
                    async with db.tenant_tx(u) as c:
                        await c.execute('update investor_research_jobs set status=$2,proposal=$3::jsonb,result_summary=$4,finished_at=now(),saved_changes=$5::jsonb where id=$1',job['id'],status,json.dumps(proposal) if proposal else None,summary,json.dumps(changes,default=str) if changes else None)
                        if status in ('needs_follow_up','failed'):
                            title='Complete investor profile: '+(profile['organization']['name'] if profile else str(job['organization_id']))
                            if not await c.fetchval("select id from crm_tasks where organization_id=$1 and title=$2 and status='open'",job['organization_id'],title):await c.execute("insert into crm_tasks(tenant_id,organization_id,assigned_to,title,task_kind) values($1::uuid,$2,$3,$4,'research')",UUID(tenant),job['organization_id'],job['requested_by'],title)
                        counts=await c.fetch('select status,count(*) total from investor_research_jobs group by status')
                    print(json.dumps({'job':str(job['id']),'status':status,'summary':summary,'counts':{r['status']:r['total'] for r in counts}}),flush=True)
                    await asyncio.sleep(11)
            finally:await guard.execute('select pg_advisory_unlock(71922027)')
    finally:await security.close_security();await db.close_db()

asyncio.run(main())
