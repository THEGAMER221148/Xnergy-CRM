"""Apply reviewed, additive CRM migrations atomically with checksum tracking.

Requires the existing migration-owner PGHOST/PGUSER/PGPASSWORD environment.
Does not regenerate credentials, seed demo data, or change user roles.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import ssl
import sys
import asyncpg

FILES = ('004_investor_crm.sql','005_company_prospects.sql','006_prospect_contacts.sql','007_import_review.sql','008_referral_sources.sql')
SQL_DIR = Path(__file__).resolve().parents[1] / 'sql'

async def migrate(options):
    connection = await asyncpg.connect(**options,timeout=30,command_timeout=120)
    applied=[]
    try:
        async with connection.transaction():
            await connection.execute('SELECT pg_advisory_xact_lock(681927103)')
            if not await connection.fetchval("SELECT to_regclass('public.dealos_schema_migrations')"):
                raise RuntimeError('Initial schema migration must be completed first')
            for filename in FILES:
                text=SQL_DIR.joinpath(filename).read_text()
                checksum=hashlib.sha256(text.encode()).hexdigest()
                version=filename.removesuffix('.sql')
                previous=await connection.fetchval('SELECT checksum FROM dealos_schema_migrations WHERE version=$1',version)
                if previous:
                    if previous!=checksum: raise RuntimeError('Migration checksum mismatch')
                    continue
                await connection.execute(text)
                await connection.execute('INSERT INTO dealos_schema_migrations(version,checksum) VALUES($1,$2)',version,checksum)
                applied.append(version)
        print('CRM migration transaction committed. Applied: '+(', '.join(applied) or 'none; already current'))
    finally: await connection.close()

if __name__=='__main__':
    try:
        asyncio.run(migrate(dict(host=os.environ['PGHOST'],port=5432,database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context())))
    except Exception as error:
        print('CRM migration failed: '+type(error).__name__+'. Credentials and SQL are not logged.',file=sys.stderr)
        sys.exit(1)
