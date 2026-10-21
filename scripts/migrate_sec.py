import sys,asyncio,os,ssl
sys.path.insert(0,'/app/scripts')
import migrate_crm
migrate_crm.FILES=('016_sec_investors.sql',)
asyncio.run(migrate_crm.migrate(dict(host=os.environ['PGHOST'],database='dealos',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],ssl=ssl.create_default_context())))
