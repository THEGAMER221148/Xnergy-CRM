import asyncio,sys,uuid
sys.path.insert(0,'/app/scripts')
import asyncpg
from migrate import migrate

async def main():
    for _ in range(60):
        try:
            root=await asyncpg.connect(host='migration-test-db',user='postgres',password='temporary-test-only',database='postgres',ssl=False)
            break
        except (OSError,asyncpg.PostgresError):
            await asyncio.sleep(1)
    else:
        raise RuntimeError('Test database did not become ready')
    await root.execute("CREATE ROLE migration_owner LOGIN CREATEDB CREATEROLE PASSWORD 'temporary-owner-only'")
    await root.execute('CREATE DATABASE dealos OWNER migration_owner')
    options=dict(host='migration-test-db',user='migration_owner',password='temporary-owner-only',database='dealos',ssl=False)
    await migrate(options,'temporary-app-only')
    await migrate(options,'temporary-app-only')
    app=await asyncpg.connect(**dict(options,user='dealos_app',password='temporary-app-only'))
    owner=await asyncpg.connect(**options)
    tenant1,tenant2,user1,user2=[uuid.uuid4() for _ in range(4)]
    for t,u in ((tenant1,user1),(tenant2,user2)):
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.tenant_id',$1,true)",str(t))
            await owner.execute("INSERT INTO tenants(id,name,slug) VALUES($1,'test',$2)",t,str(t))
            await owner.execute("INSERT INTO users(id,tenant_id,email,display_name,role) VALUES($1,$2,$3,'test','admin')",u,t,str(u)+'@example.invalid')
    assert await app.fetchval('SELECT count(*) FROM users')==0
    async with app.transaction():
        await app.execute("SELECT set_config('app.tenant_id',$1,true)",str(tenant1))
        assert await app.fetchval('SELECT count(*) FROM users')==1
        assert await app.fetchval('SELECT count(*) FROM users WHERE tenant_id=$1',tenant2)==0
        try:
            async with app.transaction():
                await app.execute("INSERT INTO users(tenant_id,email,display_name,role) VALUES($1,'forbidden@example.invalid','test','admin')",tenant2)
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError('Cross-tenant insert was accepted')
    assert await app.fetchval('SELECT count(*) FROM users')==0
    await app.close();await owner.close();await root.close()
    print('PASS: first migration, safe rerun, runtime login, role restrictions, tenant visibility, cross-tenant write rejection, context reset.',flush=True)
if __name__ == '__main__':
    asyncio.run(main())
