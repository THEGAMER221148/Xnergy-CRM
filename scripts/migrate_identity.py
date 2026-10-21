"""Apply the identity login policy once, preserving the initial migration."""
import asyncio
import hashlib
import os
from pathlib import Path
import ssl
import sys

import asyncpg

SQL = Path(__file__).resolve().parents[1] / 'sql' / '003_identity_login.sql'
VERSION = '003_identity_login'


async def migrate_identity(options):
    sql = SQL.read_text()
    checksum = hashlib.sha256(sql.encode()).hexdigest()
    con = await asyncpg.connect(**options, timeout=30, command_timeout=120)
    try:
        async with con.transaction():
            await con.execute('select pg_advisory_xact_lock(681927103)')
            if not await con.fetchval("select exists(select 1 from public.dealos_schema_migrations where version='001_initial')"):
                raise RuntimeError('Initial migration required')
            previous = await con.fetchval('select checksum from public.dealos_schema_migrations where version=$1', VERSION)
            if previous is not None:
                if previous != checksum:
                    raise RuntimeError('Migration checksum mismatch')
                print('Identity login policy already applied; no changes made.', flush=True)
            else:
                await con.execute(sql)
                await con.execute('insert into public.dealos_schema_migrations(version,checksum) values($1,$2)', VERSION, checksum)
                print('Identity login policy and restricted provisioning permissions applied.', flush=True)
            policy = await con.fetchval("select count(*) from pg_policies where schemaname='public' and tablename='users' and policyname='provisioned_identity_login' and cmd='SELECT'")
            writable = await con.fetchval("select has_table_privilege('dealos_app','public.auth_identities','INSERT,UPDATE,DELETE')")
            timestamp = await con.fetchval("select has_column_privilege('dealos_app','public.auth_identities','last_login_at','UPDATE')")
            forced = await con.fetchval("select relrowsecurity and relforcerowsecurity from pg_class where oid='public.users'::regclass")
            if policy != 1 or writable or not timestamp or not forced:
                raise RuntimeError('Identity policy verification failed')
        print('Identity policy verified; forced tenant row security remains enabled.', flush=True)
    finally:
        await con.close()


if __name__ == '__main__':
    try:
        asyncio.run(migrate_identity(dict(host=os.environ['PGHOST'], port=5432, database='dealos', user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'], ssl=ssl.create_default_context())))
    except Exception as error:
        print('Identity migration failed: ' + type(error).__name__ + '. No credentials are logged.', file=sys.stderr)
        sys.exit(1)
