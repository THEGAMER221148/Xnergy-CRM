"""Explicit investor-list imports, with deterministic columns and durable research queue."""
import base64,csv,io,json,hashlib,re,zipfile
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from .intake_sources import MAX_BYTES,IntakeSourceError

ALIASES={'firm_name':{'company','companyname','firm','firmname','investor','investorname','organization','organisation'},'contact_name':{'contact','contactname','name','fullname'},'email':{'email','emailaddress','contactemail'},'phone':{'phone','phonenumber','telephone','contactphone'},'website_url':{'website','websiteurl','url','firmwebsite'},'description':{'description','summary','companydescription'},'firm_type':{'investortype','category','investorcategory'},'sector_focus':{'sector','sectors','sectorfocus','industry','industries'},'title':{'title','jobtitle','role'},'notes':{'notes','note'},'firm_phone':{'firmphone','mainphone'},'firm_email':{'firmemail','generalemail'}}
def key(x):return re.sub('[^a-z0-9]','',str(x or '').lower())
def parse_rows(filename,encoded,aliases=None,diagnostics=False):
    aliases=aliases or ALIASES
    try:raw=base64.b64decode(encoded,validate=True)
    except Exception:raise HTTPException(422,'Invalid file encoding.')
    if not raw or len(raw)>MAX_BYTES:raise HTTPException(422,'Choose a CSV or XLSX file up to 5 MB.')
    if filename.lower().endswith('.csv'):
        try:rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        except Exception:raise HTTPException(422,'Save the CSV with UTF-8 encoding.')
    elif filename.lower().endswith('.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if len(z.infolist())>2000 or sum(i.file_size for i in z.infolist())>20*1024*1024:raise ValueError()
                def xml(p):
                    data=z.read(p)
                    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:raise ValueError()
                    return ET.fromstring(data)
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                strings=[]
                if 'xl/sharedStrings.xml' in z.namelist():strings=[''.join(x.itertext()) for x in xml('xl/sharedStrings.xml').findall('s:si',ns)]
                sheets=sorted(p for p in z.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml$',p))
                if len(sheets)!=1:raise HTTPException(422,'Upload one worksheet at a time, or export the investor worksheet as CSV.')
                rows=[]
                for row in xml(sheets[0]).findall('.//s:row',ns):
                    cells={}
                    for c in row.findall('s:c',ns):
                        col=0
                        for letter in re.sub('[0-9]','',c.get('r','A')):col=col*26+ord(letter)-64
                        v=c.find('s:v',ns);value=v.text if v is not None else ''.join(c.find('s:is',ns).itertext()) if c.find('s:is',ns) is not None else ''
                        if c.get('t')=='s':value=strings[int(value)]
                        cells[col-1]=value
                    rows.append([cells.get(i,'') for i in range(max(cells,default=-1)+1)])
        except HTTPException:raise
        except Exception:raise HTTPException(422,'Could not read this XLSX. Export its investor worksheet as CSV.')
    else:raise HTTPException(422,'Use CSV or XLSX for investor lists.')
    rows=[r for r in rows if any(str(v or '').strip() for v in r)]
    if len(rows)>10001:raise HTTPException(422,'Split lists into files with at most 10,000 investors.')
    if not rows:raise HTTPException(422,'The file is empty.')
    headers={i:next((k for k,a in aliases.items() if key(v) in a),None) for i,v in enumerate(rows[0])}
    mapped=[v for v in headers.values() if v]
    if len(mapped)!=len(set(mapped)):raise HTTPException(422,'Multiple columns map to the same field; use one column per field.')
    if 'firm_name' not in headers.values():raise HTTPException(422,'Include a Company, Firm name, or Investor name column. Optional columns: Contact, Email, Phone, Website, Category, Sector, Description.')
    result=(hashlib.sha256(raw).hexdigest(),[{headers[i]:str(v or '').strip() for i,v in enumerate(row) if headers.get(i)} for row in rows[1:]])
    return (*result,[str(v) for i,v in enumerate(rows[0]) if v and not headers.get(i)]) if diagnostics else result

async def import_rows(filename,encoded,user,banker_id=None,offset=0,limit=None):
    from .crm import Contact,insert_contact,audit
    from .crm_models import Firm
    from .db import tenant_tx
    digest,rows=parse_rows(filename,encoded)
    report={'created':0,'matched':0,'contacts_added':0,'already_imported':0,'queued':0,'errors':[],'rows':len(rows)}
    rows=rows[offset:offset+limit] if limit is not None else rows[offset:]
    for n,r in enumerate(rows,offset+2):
        try:
            if not r.get('firm_name'):raise ValueError('Missing investor / firm name')
            website=r.get('website_url') or None
            if website and not website.startswith(('https://','http://')):website='https://'+website
            firm=Firm(name=r['firm_name'],website_url=website,**{k:r[k] for k in ('description','firm_type','sector_focus','notes','firm_phone','firm_email') if r.get(k)})
            contact=Contact(name=r.get('contact_name') or None,email=r.get('email') or None,phone=r.get('phone') or None,title=r.get('title') or None)
            async with tenant_tx(user) as c:
                source='investor-list:'+digest+':'+str(n)
                owner=await c.fetchval("select display_name from users where id=$1::uuid and active and relationship_banker",str(banker_id or user['user_id']))
                if not owner:raise ValueError('Choose an active relationship banker')
                await c.execute('select pg_advisory_xact_lock(hashtextextended($1,0))',str(user['tenant_id'])+':investor:'+firm.name.casefold())
                if await c.fetchval('select id from crm_source_records where source_key=$1',source):report['already_imported']+=1;continue
                existing=await c.fetchrow('select * from investor_organizations where lower(name)=lower($1) and relationship_owner=$2 order by created_at limit 1',firm.name,owner)
                if banker_id and not existing and await c.fetchval('select id from investor_organizations where lower(name)=lower($1)',firm.name):raise ValueError('Investor exists under another banker; review assignment before importing.')
                new=not existing
                if new:
                    fields=list(Firm.model_fields);firm.relationship_owner=owner
                    org=await c.fetchrow('insert into investor_organizations(tenant_id,'+','.join(fields)+') values($1::uuid,'+','.join('$'+str(i+2) for i in range(len(fields)))+') returning *',user['tenant_id'],*[getattr(firm,k) for k in fields])
                else:
                    org=existing
                    for k,v in firm.model_dump(exclude_unset=True).items():
                        if k!='name' and v and not org[k]:await c.execute(f'update investor_organizations set {k}=$2,updated_at=now() where id=$1',org['id'],v)
                added=False
                if any((contact.name,contact.email,contact.phone)):
                    duplicate=await c.fetchval('select id from investor_contacts where (email is not null and lower(email)=lower($1)) or (organization_id=$2 and name=$3)',contact.email,org['id'],contact.name)
                    if not duplicate:await insert_contact(c,user,'investor',org['id'],contact);added=True
                await c.execute("insert into crm_source_records(tenant_id,organization_id,source_key,filename,row_number,file_sha256,raw_record,normalized_record,status) values($1::uuid,$2,$3,$4,$5,$6,$7::jsonb,$7::jsonb,'imported_investor')",user['tenant_id'],org['id'],source,filename,n,digest,json.dumps(r))
                queued=await c.execute('insert into investor_research_jobs(tenant_id,organization_id,requested_by) values($1::uuid,$2,$3::uuid) on conflict(tenant_id,organization_id) do nothing',user['tenant_id'],org['id'],user['user_id'])
                await audit(c,user,'INVESTOR_LIST_IMPORTED','investor_organization',org['id'])
            report['created' if new else 'matched']+=1;report['contacts_added']+=int(added);report['queued']+=int(queued.split()[-1])
        except Exception as e:
            message=str(e.detail) if isinstance(e,HTTPException) else str(e) if isinstance(e,ValueError) else type(e).__name__
            report['errors'].append({'row':n,'message':message[:300]})
    return report
