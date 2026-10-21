"""Exercise the login policy against temporary PostgreSQL, never Azure data."""
import asyncio
import sys
import uuid
from pathlib import Path

import asyncpg
from fastapi import HTTPException

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')
from migrate import migrate
from migrate_identity import migrate_identity
from app.security import resolve_identity


async def main():
    for _ in range(60):
        try:
            root = await asyncpg.connect(host='identity-test-db', user='postgres', password='temporary-test-only', database='postgres', ssl=False)
            break
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(1)
    else:
        raise RuntimeError('Test database did not become ready')
    await root.execute("CREATE ROLE migration_owner LOGIN CREATEDB CREATEROLE PASSWORD 'temporary-owner-only'")
    await root.execute('CREATE DATABASE dealos OWNER migration_owner')
    options = dict(host='identity-test-db', user='migration_owner', password='temporary-owner-only', database='dealos', ssl=False)
    await migrate(options, 'temporary-app-only')
    owner = await asyncpg.connect(**options)
    await migrate_identity(options)
    await migrate_identity(options)
    app = await asyncpg.connect(**dict(options, user='dealos_app', password='temporary-app-only'))
    issuer = 'https://issuer.example.invalid'
    tenant1, tenant2, user1, user2 = [uuid.uuid4() for _ in range(4)]
    for tenant, user in ((tenant1, user1), (tenant2, user2)):
        async with owner.transaction():
            await owner.execute("select set_config('app.tenant_id',$1,true)", str(tenant))
            await owner.execute("insert into tenants(id,name,slug) values($1,'test',$2)", tenant, str(tenant))
            await owner.execute("insert into users(id,tenant_id,email,display_name,role) values($1,$2,'same@example.invalid','test','admin')", user, tenant)
            await owner.execute("insert into auth_identities(user_id,issuer,subject) values($1,$2,$3)", user, issuer, str(user))
    assert await app.fetchval('select count(*) from users') == 0
    for user, tenant in ((user1, tenant1), (user2, tenant2)):
        result = await resolve_identity(app, {'iss': issuer, 'sub': str(user)})
        assert result['id'] == user and result['tenant_id'] == tenant
        assert await app.fetchval('select count(*) from users') == 0
        assert await app.fetchval("select nullif(current_setting('app.auth_subject',true),'')") is None
    for claims in ({'iss': issuer, 'sub': 'unknown', 'email': 'same@example.invalid'}, {'iss': 'wrong', 'sub': str(user1)}, {'iss': issuer}):
        try:
            await resolve_identity(app, claims)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError('Unprovisioned identity accepted')
    async with owner.transaction():
        await owner.execute("select set_config('app.tenant_id',$1,true)", str(tenant1))
        await owner.execute('update users set active=false where id=$1', user1)
    try:
        await resolve_identity(app, {'iss': issuer, 'sub': str(user1)})
    except HTTPException as error:
        assert error.status_code == 403
    else:
        raise AssertionError('Inactive user accepted')
    async with app.transaction():
        await app.execute("select set_config('app.auth_issuer',$1,true)", issuer)
        await app.execute("select set_config('app.auth_subject',$1,true)", str(user2))
        assert await app.fetchval('select count(*) from users') == 1
        assert await app.execute("update users set display_name='forbidden' where id=$1", user2) == 'UPDATE 0'
    for sql in ("insert into auth_identities(user_id,issuer,subject) values($1,'forged','forged')", "update auth_identities set subject='forged' where user_id=$1", "delete from auth_identities where user_id=$1"):
        try:
            await app.execute(sql, user2)
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError('Application can change provisioned identities')
    assert await app.fetchval('select count(*) from users') == 0
    await app.close()
    await owner.close()
    await root.close()
    print('PASS: identity login across two tenants, same-email separation, unknown/wrong issuer/inactive rejection, context reset, SELECT-only policy, and administrator-only provisioning.', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
