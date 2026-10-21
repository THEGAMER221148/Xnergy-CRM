import base64,io,zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import bulk_import as b,prospect_shortlist as s
from app.investor_import import parse_rows
ID='22222222-2222-4222-8222-222222222222'
USER={'user_id':ID,'tenant_id':'11111111-1111-4111-8111-111111111111','role':'banker'}
def upload(text,**kw):return b.Upload(filename='test.csv',data=base64.b64encode(text.encode()).decode(),kind='prospect',banker_id=ID,**kw)
@pytest.mark.parametrize('value,units,expected',[('5','millions',5000000),('$5M','millions',5000000),('(250,000)','whole',-250000),('0','whole',0),('1.25','thousands',1250)])
def test_amount(value,units,expected):assert b.amount(value,units)==expected
@pytest.mark.parametrize('value',['5-10','10%','unknown','NaN','1e9','approx 5'])
def test_ambiguous_amount(value):
 with pytest.raises(ValueError):b.amount(value,'whole')
def test_partial_row_and_negative_ebitda():
 body=upload('Company,Revenue,EBITDA,Stage\nAcme,5M,(500K),Engagement sent')
 _,rows=parse_rows(body.filename,body.data,aliases=b.PROSPECT_ALIASES)
 p,c,stage=b.prospect(rows[0],body)
 assert p.company_profile.annual_revenue==5000000
 assert p.company_profile.ebitda==-500000
 assert p.owner_user_id==UUID(ID) and stage=='engagement_sent'
 p,_,_=b.prospect({'firm_name':'Only a name'},body)
 assert p.company_profile.annual_revenue is None

def test_duplicate_columns_rejected():
 body=upload('Company,Firm\nAcme,Other')
 with pytest.raises(Exception):parse_rows(body.filename,body.data)

def test_exports_exclude_private_fields_and_escape_formulas():
 rows=[{'name':'=HYPERLINK("evil")','website_url':'https://example.com','relationship_owner':'Marc','firm_email':'private@example.com','notes':'SECRET','status':'shortlisted'}]
 client=s.export_csv(rows,'client');banker=s.export_csv(rows,'banker')
 assert 'private@example.com' not in client and 'Marc' not in client and 'SECRET' not in client
 assert "'=HYPERLINK" in client and 'private@example.com' in banker

@pytest.fixture
def harness(monkeypatch):
 c=AsyncMock();c.fetchrow.return_value={'id':UUID(ID),'display_name':'Marc Carroll'};c.fetchval.return_value=None
 @asynccontextmanager
 async def tx(u):
  assert u==USER
  yield c
 monkeypatch.setattr(b,'tenant_tx',tx);monkeypatch.setattr(b,'require_user',AsyncMock(return_value=USER));monkeypatch.setattr(b,'enforce_csrf',AsyncMock())
 app=FastAPI();app.include_router(b.router)
 return TestClient(app),c

def test_preview_does_not_write_and_uses_selected_banker(harness):
 client,c=harness;body=upload('Company,Revenue\nAcme,5',units='millions')
 r=client.post('/api/spreadsheet-import',json=body.model_dump(mode='json'))
 assert r.status_code==200,r.text
 assert r.json()['preview'][0]['values']['company_profile']['annual_revenue']==5000000
 c.execute.assert_not_awaited()

def test_invalid_banker_blocks_import(harness):
 client,c=harness;c.fetchrow.return_value=None
 r=client.post('/api/spreadsheet-import',json=upload('Company\nAcme',preview=False).model_dump(mode='json'))
 assert r.status_code==422
 c.execute.assert_not_awaited()

def test_duplicate_is_not_overwritten(harness):
 client,c=harness;c.fetchval.side_effect=[None,UUID(ID)]
 r=client.post('/api/spreadsheet-import',json=upload('Company\nAcme',preview=False).model_dump(mode='json'))
 assert r.status_code==200,r.text
 assert r.json()['duplicates']==1
 assert all('update ' not in call.args[0].lower() and 'insert ' not in call.args[0].lower() for call in c.execute.call_args_list)

def test_repeat_file_skips(harness):
 client,c=harness;c.fetchval.return_value=UUID(ID)
 r=client.post('/api/spreadsheet-import',json=upload('Company\nAcme',preview=False).model_dump(mode='json'))
 assert r.json()['already_imported']==1

def test_creates_prospect_contact_pipeline_and_source_atomically(harness,monkeypatch):
 from app import pipeline
 client,c=harness;c.fetchval.side_effect=[None,None,UUID(ID)]
 ensure=AsyncMock(return_value={'id':UUID(ID),'stage':'initial_identification'});monkeypatch.setattr(pipeline,'ensure_deal',ensure)
 r=client.post('/api/spreadsheet-import',json=upload('Company,Revenue\nAcme,5M',preview=False).model_dump(mode='json'))
 assert r.status_code==200,r.text
 assert r.json()['created']==1,r.text
 query=c.fetchval.call_args_list[-1]
 assert '$15::jsonb' in query.args[0] and len(query.args)==16
 ensure.assert_awaited_once()
 assert any('crm_source_records' in call.args[0] for call in c.execute.call_args_list)

def test_assignment_does_not_grant_access(monkeypatch):
 c=AsyncMock();c.fetchrow.return_value={'id':UUID(ID)};c.fetchval.return_value=UUID(ID)
 @asynccontextmanager
 async def tx(u):yield c
 monkeypatch.setattr(s,'tenant_tx',tx);monkeypatch.setattr(s,'require_user',AsyncMock(return_value=USER));csrf=AsyncMock();monkeypatch.setattr(s,'enforce_csrf',csrf);monkeypatch.setattr(s,'ensure_deal',AsyncMock(return_value={'id':UUID(ID)}))
 app=FastAPI();app.include_router(s.router)
 r=TestClient(app).post('/api/prospects/'+ID+'/investor-shortlist',json={'organization_id':ID})
 assert r.status_code==200,r.text
 query=c.fetchval.call_args.args[0]
 assert 'values($1::uuid,$2,$3,0,$4)' in query
 assert 'access_level=' not in query.split('do update')[1]
 csrf.assert_awaited_once()

def test_investor_selected_banker_forwarded(harness,monkeypatch):
 from app import investor_import
 client,c=harness;body=upload('Company,Email\nExample Fund,person@example.com',preview=False);body.kind='investor'
 imp=AsyncMock(return_value={'created':1,'errors':[]});monkeypatch.setattr(investor_import,'import_rows',imp)
 r=client.post('/api/spreadsheet-import',json=body.model_dump(mode='json'))
 assert r.status_code==200,r.text
 assert imp.call_args.kwargs['banker_id']==UUID(ID)
 assert imp.call_args.args[2]==USER

def test_xlsx_numeric_cells_preserve_amounts():
 buf=io.BytesIO()
 with zipfile.ZipFile(buf,'w') as z:
  z.writestr('xl/worksheets/sheet1.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Company</t></is></c><c r="B1" t="inlineStr"><is><t>Revenue</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Acme</t></is></c><c r="B2"><v>5123456</v></c></row></sheetData></worksheet>''')
 body=b.Upload(filename='test.xlsx',data=base64.b64encode(buf.getvalue()).decode(),kind='prospect',banker_id=ID)
 _,rows=parse_rows(body.filename,body.data,aliases=b.PROSPECT_ALIASES)
 p,_,_=b.prospect(rows[0],body)
 assert p.company_profile.annual_revenue==5123456

def test_unmapped_columns_reported():
 body=upload('Company,Revenue estimate\nAcme,5M')
 _,_,ignored=parse_rows(body.filename,body.data,aliases=b.PROSPECT_ALIASES,diagnostics=True)
 assert ignored==['Revenue estimate']
