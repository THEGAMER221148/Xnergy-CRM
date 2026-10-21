"""Initialize an empty DealOS database; never log credentials or seed demo data."""
import asyncio
import hashlib
import os
from pathlib import Path
import ssl
import sys
import asyncpg

SQL_DIR = Path(__file__).resolve().parents[1] / 'sql'
FILES = ('001_schema.sql', '002_roles.sql')
VERSION = '001_initial'

async def migrate(options, app_password):
    if not app_password or app_password == 'CHANGE_ME':
        raise ValueError('Application password is missing or is a placeholder')
    texts = [SQL_DIR.joinpath(name).read_text() for name in FILES]
    checksum = hashlib.sha256('\n'.join(texts).encode()).hexdigest()
    con = await asyncpg.connect(**options, timeout=30, command_timeout=120)
    try:
        async with con.transaction():
            await con.execute('SELECT pg_advisory_xact_lock(681927103)')
            ledger = await con.fetchval("SELECT to_regclass('public.dealos_schema_migrations')")
            if ledger:
                previous = await con.fetchval('SELECT checksum FROM public.dealos_schema_migrations WHERE version=$1', VERSION)
                if previous != checksum:
                    raise RuntimeError('Migration history mismatch; manual review required')
                print('Schema already initialized; no changes made.', flush=True)
            else:
                tables = await con.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                role = await con.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='dealos_app')")
                if tables or role:
                    raise RuntimeError('Expected an empty database and no existing application role; manual review required')
                for sql in texts:
                    await con.execute(sql)
                # Bind the password as data; do not put it in a logged SQL literal.
                await con.execute("SELECT set_config('dealos.bootstrap_password', $1, true)", app_password)
                await con.execute("""DO $body$ BEGIN
                  EXECUTE format('ALTER ROLE dealos_app LOGIN PASSWORD %L',
                                 current_setting('dealos.bootstrap_password'));
                END $body$;""")
                await con.execute("SELECT set_config('dealos.bootstrap_password', '', true)")
                await con.execute('CREATE TABLE public.dealos_schema_migrations(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
                await con.execute('REVOKE ALL ON public.dealos_schema_migrations FROM PUBLIC, dealos_app')
                await con.execute('INSERT INTO public.dealos_schema_migrations(version,checksum) VALUES($1,$2)',VERSION,checksum)
                print('Schema and restricted application role created.', flush=True)
        runtime = dict(options, user='dealos_app', password=app_password)
        app = await asyncpg.connect(**runtime, timeout=30)
        try:
            flags = await app.fetchrow("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
            if any(flags.values()):
                raise RuntimeError('Application role has excessive privileges')
            owns = await app.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user")
            protected = await app.fetchval("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY($1::text[]) AND c.relrowsecurity AND c.relforcerowsecurity",['users','investor_organizations','investor_contacts','deals','deal_investors','interactions','documents','audit_events'])
            if owns or protected != 8:
                raise RuntimeError('Database ownership or row security verification failed')
            await app.fetchval('SELECT count(*) FROM public.users')
            print('Application login, non-owner privileges, and eight forced row-security policies verified.',flush=True)
        finally:
            await app.close()
    finally:
        await con.close()

async def main():
    options = dict(host=os.environ['PGHOST'], port=5432, database='dealos', user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'], ssl=ssl.create_default_context())
    await migrate(options, os.environ['DEALOS_APP_PASSWORD'])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        # Avoid exception text, SQL, DSNs or tracebacks that can contain secrets.
        print('Migration failed: ' + type(error).__name__ + '. No credentials are logged.', file=sys.stderr)
        sys.exit(1)
