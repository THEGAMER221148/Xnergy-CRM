from app import research_sources as s
from app.investor_research import automatic_edit

def test_exact_match_ambiguous_and_conflict(monkeypatch):
    record={'crd':'1','names':['Example Capital LLC'],'website_url':'https://example.com'}
    monkeypatch.setattr(s,'directory',lambda:({}, {'examplecapital':[record]}))
    p={'organization':{'name':'Example Capital'}}
    assert s.sec_match(p)==record
    p['organization']['website_url']='https://unrelated.com'
    assert s.sec_match(p) is None
    p['organization'].pop('website_url')
    monkeypatch.setattr(s,'directory',lambda:({}, {'examplecapital':[record,{**record,'crd':'2'}]}))
    assert s.sec_match(p) is None

def test_sec_never_invents_contact_person_or_criteria():
    row={'crd':'123','firm_phone':'212-555-0100','filing_date':'2026-01-01','website_url':'https://example.com'}
    record=s.sec_record(row,'Example Capital')
    profile={'revision':'test','organization':{'name':'Example Capital','relationship_status':'unknown'},'contacts':[],'mandates':[]}
    edit=automatic_edit(profile,{'website_url':row['website_url'],'records':[record],'sources':['https://adviserinfo.sec.gov/firm/summary/123']})
    assert edit.organization.firm_phone=='212-555-0100'
    assert not edit.contacts and not edit.mandates
    assert edit.organization.description is None

def test_snapshot_size_and_named_firm():
    data,_=s.directory()
    assert len(data['records'])==23835
    row=s.sec_match({'organization':{'name':'UP PARTNERS MANAGEMENT COMPANY, LLC'}})
    assert row and row['crd']=='309765'
    assert s.website(row['website_url'])=='https://www.up.partners/'

def test_holding_sector_is_searchable_but_not_confirmed():
    from app.prototype_search import InvestorSearch,match_criteria
    org={'observed_holding_sectors':'Healthcare; Technology'}
    s=InvestorSearch(unknown=True,selections={'sector':['Healthcare']})
    result=match_criteria(org,{},s)
    assert result and 'investment focus unconfirmed' in result['missing'][0]
    s.unknown=False
    assert match_criteria(org,{},s) is None
    s.unknown=True
    assert match_criteria(org,{'excluded_sectors':['Healthcare']},s) is None

def test_sic_is_industry_not_stage_or_check_size():
    from app.sec_industries import holding_sector
    assert holding_sector('2834')=='Healthcare'
    assert holding_sector('3674')=='Technology'
    assert holding_sector(None) is None

def test_current_quarter_parser_keeps_owner_and_issuer_separate():
    from app.sec_recent import parse_filing,index_filings
    assert index_filings('1|Issuer|4|2026-09-20|edgar/data/1/0000000001-26-000001.txt')
    raw=b'''FILED AS OF DATE: 20260920\n<ownershipDocument><documentType>4</documentType><issuer><issuerCik>1</issuerCik><issuerName>Health Example</issuerName><issuerTradingSymbol>HEX</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik><rptOwnerName>Example Person</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner></ownershipDocument>'''
    r=parse_filing(raw,'0000000001-26-000001','edgar/data/1/0000000001-26-000001.txt')[0]
    assert r['name']=='Example Person' and r['issuer_name']=='Health Example'
    assert r['source']=='SEC' and r['form']=='4'
    assert 'check_min' not in r and 'email' not in r
