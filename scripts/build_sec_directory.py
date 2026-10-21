"""Build a dated, auditable public SEC ADV directory from official CSV archives."""
import csv,io,json,sys,zipfile
from pathlib import Path
BASE='https://www.sec.gov/files/investment/data/other/information-about-registered-investment-advisers-exempt-reporting-advisers/'
records=[]
for kind in ('exempt','registered'):
    with zipfile.ZipFile('/tmp/dealos-sec-'+kind+'.zip') as z:
        raw=z.read(next(n for n in z.namelist() if n.lower().endswith('.csv')))
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:text=raw.decode('cp1252')
    for row in csv.DictReader(io.StringIO(text)):
        names=list(dict.fromkeys(row[k].strip() for k in ('Primary Business Name','Legal Name') if row.get(k)))
        record={'names':names,'crd':row['Organization CRD#'],'source':BASE+'ia09012026-'+kind+'.zip','filing_date':row.get('Latest ADV Filing Date'),'website_url':row.get('Website Address'),'firm_phone':row.get('Main Office Telephone Number')}
        if row.get('Main Office Private Residence Flag')!='Y':
            record.update(street_address=' '.join(row.get(k,'') for k in ('Main Office Street Address 1','Main Office Street Address 2')).strip(),city=row.get('Main Office City'),state_region=row.get('Main Office State'),postal_code=row.get('Main Office Postal Code'),country=row.get('Main Office Country'))
        records.append(record)
out=Path(__file__).resolve().parents[1]/'app'/'data';out.mkdir(exist_ok=True)
(out/'sec_advisers.json').write_text(json.dumps({'as_of':'2026-09-01','records':records},separators=(',',':')))
print('SEC directory entries:',len(records))
