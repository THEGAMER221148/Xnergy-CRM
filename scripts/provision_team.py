"""Provision only the explicitly approved directory-account manifest."""
import asyncio,json,os,ssl,sys
from pathlib import Path
from provision_admin import provision
async def main():
    rows=json.loads(Path('/app/approved-users.json').read_text())
    if len({r['email'] for r in rows})!=len(rows) or len({r['object_id'] for r in rows})!=len(rows):
        raise ValueError('Duplicate approved identity')
    options=dict(host=os.environ['PGHOST'],port=5432,database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context())
    for row in rows:
        await provision(options,os.environ['DEALOS_APP_PASSWORD'],os.environ['ADMIN_ISSUER'],row['object_id'],row['email'],row['role'],row['display_name'])
    print('Approved team account count verified:',len(rows),flush=True)
if __name__=='__main__':
    try: asyncio.run(main())
    except Exception as error:
        print('Team provisioning stopped: '+type(error).__name__+'. No credentials are logged.',file=sys.stderr)
        sys.exit(1)
