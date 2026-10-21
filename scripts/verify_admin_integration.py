import asyncio
import sys
import uuid

import asyncpg
from fastapi import HTTPException

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')
from migrate import migrate
from migrate_identity import migrate_identity
from provision_admin import provision
from app.security import resolve_identity


async def main():
    for _ in range(60):
        try:
            root = await asyncpg.connect(host='admin-test-db',user='postgres',password='temporary-test-only',database='postgres',ssl=False)
            break
        except (OSError,asyncpg.PostgresError):
            await asyncio.sleep(1)
    else:
        raise RuntimeError('Test database unavailable')
    await root.execute("create role migration_owner login createdb createrole password 'temporary-owner-only'")
    await root.execute('create database dealos owner migration_owner')
    options=dict(host='admin-test-db',user='migration_owner',password='temporary-owner-only',database='dealos',ssl=False)
    await migrate(options,'temporary-app-only')
    await migrate_identity(options)
    directory,oid=str(uuid.uuid4()),str(uuid.uuid4())
    issuer=f'https://login.microsoftonline.com/{directory}/v2.0'
    email='approved@example.invalid'
    user,tenant=await provision(options,'temporary-app-only',issuer,oid,email)
    assert await provision(options,'temporary-app-only',issuer,oid,email)==(user,tenant)
    app=await asyncpg.connect(**dict(options,user='dealos_app',password='temporary-app-only'))
    claims={'iss':issuer,'tid':directory,'oid':oid,'sub':'test-pairwise'}
    assert (await resolve_identity(app,claims))['role']=='admin'
    for wrong in ({**claims,'oid':str(uuid.uuid4()),'email':email},{**claims,'tid':str(uuid.uuid4())}):
        try:
            await resolve_identity(app,wrong)
        except HTTPException as error:
            assert error.status_code==403
        else:
            raise AssertionError('Unapproved identity received access')
    try:
        await provision(options,'temporary-app-only',issuer,str(uuid.uuid4()),email)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Existing identity was relinked')
    owner=await asyncpg.connect(**options)
    async with owner.transaction():
        await owner.execute("select set_config('app.tenant_id',$1,true)",str(tenant))
        await owner.execute("select set_config('app.user_role','admin',true)")
        assert await owner.fetchval("select count(*) from audit_events where event_type='ADMIN_PROVISIONED'")==1
        assert await owner.fetchval('select count(*) from users')==1
        await owner.execute("update users set role='banker' where id=$1",user)
    try:
        await provision(options,'temporary-app-only',issuer,oid,email)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Existing account was promoted silently')
    assert (await resolve_identity(app,claims))['role']=='banker'
    member_oid=str(uuid.uuid4())
    member=await provision(options,'temporary-app-only',issuer,member_oid,'member@example.invalid','banker','Team Member')
    assert await provision(options,'temporary-app-only',issuer,member_oid,'member@example.invalid','banker','Team Member')==member
    assert (await resolve_identity(app,dict(claims,oid=member_oid)))['role']=='banker'
    assert (await resolve_identity(app,dict(claims,oid=member_oid)))['tenant_id']==tenant
    try:
        await provision(options,'temporary-app-only',issuer,member_oid,'member@example.invalid','admin','Team Member')
    except RuntimeError: pass
    else: raise AssertionError('Team member silently promoted')
    await app.close();await owner.close();await root.close()
    print('PASS: administrator creation, safe rerun, approved Entra identity login, wrong identity/tenant rejection, no relinking, no silent promotion, and single audit record.',flush=True)


if __name__=='__main__':
    asyncio.run(main())
