"""Provision one explicitly approved Entra administrator, without changing credentials."""
import asyncio
import os
import ssl
import sys
import uuid

import asyncpg

sys.path.insert(0, '/app')
from app.security import identity_key, resolve_identity


async def provision(options, app_password, issuer, object_id, email, role="admin", display_name="Nia"):
    # Only tenant-specific Entra v2 identities are supported by this operator job.
    issuer = issuer.rstrip('/')
    parts = issuer.split('/')
    if len(parts) != 5 or parts[:3] != ['https:', '', 'login.microsoftonline.com'] or parts[4] != 'v2.0':
        raise ValueError('Expected tenant-specific Entra issuer')
    claims = {'iss': issuer, 'sub': 'operator-verification', 'tid': str(uuid.UUID(parts[3])), 'oid': str(uuid.UUID(object_id))}
    issuer, subject = identity_key(claims)
    email = email.strip().lower()
    if not email or '@' not in email:
        raise ValueError('Email required')
    if role not in ('admin', 'banker') or not display_name.strip():
        raise ValueError('Unsupported role or empty display name')
    con = await asyncpg.connect(**options, timeout=30, command_timeout=120)
    try:
        async with con.transaction():
            await con.execute('select pg_advisory_xact_lock(681927103)')
            if not await con.fetchval("select exists(select 1 from dealos_schema_migrations where version='003_identity_login')"):
                raise RuntimeError('Identity policy migration is required')
            tenant = await con.fetchval("select id from tenants where slug='xnergy'")
            if tenant is None:
                tenant = await con.fetchval("insert into tenants(name,slug) values('Xnergy','xnergy') returning id")
            await con.execute("select set_config('app.tenant_id',$1,true)", str(tenant))
            await con.execute("select set_config('app.user_role','admin',true)")
            user = await con.fetchrow('select * from users where tenant_id=$1 and email=$2', tenant, email)
            created = user is None
            if created:
                user = await con.fetchrow("insert into users(tenant_id,email,display_name,role) values($1,$2,$3,$4) returning *", tenant, email, display_name, role)
            elif not user['active'] or user['role'] != role or user['investor_contact_id'] is not None:
                raise RuntimeError('Existing account conflicts with requested administrator; no promotion performed')
            bound = await con.fetchval('select user_id from auth_identities where issuer=$1 and subject=$2', issuer, subject)
            if bound is not None and bound != user['id']:
                raise RuntimeError('Identity is bound to another account')
            if bound is None:
                if await con.fetchval('select count(*) from auth_identities where user_id=$1', user['id']):
                    raise RuntimeError('Existing user has another identity; no relinking performed')
                await con.execute('insert into auth_identities(user_id,issuer,subject,email) values($1,$2,$3,$4)', user['id'],issuer,subject,email)
                await con.execute("select audit_event($1,$2,'user',$1,'{}'::jsonb)", str(user['id']), 'ADMIN_PROVISIONED' if role == 'admin' else 'USER_PROVISIONED')
            print('Approved administrator identity provisioned.' if created else 'Approved administrator already provisioned; identity verified.', flush=True)
        app = await asyncpg.connect(**dict(options,user='dealos_app',password=app_password),timeout=30)
        try:
            resolved = await resolve_identity(app, claims)
            if resolved['id'] != user['id'] or resolved['tenant_id'] != tenant or resolved['role'] != role:
                raise RuntimeError('Runtime identity resolution failed')
            if await app.fetchval('select count(*) from users') != 0:
                raise RuntimeError('Login context leaked after transaction')
        finally:
            await app.close()
        print('Runtime administrator identity resolution and tenant-context cleanup verified.', flush=True)
        return user['id'], tenant
    finally:
        await con.close()


if __name__ == '__main__':
    try:
        asyncio.run(provision(
            dict(host=os.environ['PGHOST'],port=5432,database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context()),
            os.environ['DEALOS_APP_PASSWORD'],os.environ['ADMIN_ISSUER'],os.environ['ADMIN_OBJECT_ID'],os.environ['ADMIN_EMAIL']))
    except Exception as error:
        print('Administrator provisioning failed: '+type(error).__name__+'. No credentials are logged.',file=sys.stderr)
        sys.exit(1)
