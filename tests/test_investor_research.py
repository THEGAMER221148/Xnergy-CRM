import pytest
from fastapi import HTTPException
from app.investor_research import website_candidate,automatic_edit
from app.crm import InvestorEdit

def profile():
 return {'revision':'abc','organization':{'name':'01 Advisors','description':'Original description','relationship_status':'unknown'},'contacts':[{'id':'00000000-0000-0000-0000-000000000001','name':'Matt Bocci','email':'bocci@01a.com','title':'Original title'}],'mandates':[]}
def proposal():
 return {'website_url':'https://01a.com','sources':['https://01a.com/about'],'records':[{'firm_name':'01 Advisors','category':'investor_buyer','first_name':'Matt','last_name':'Bocci','email':'bocci@01a.com','phone':'+1 212 555 0100','title':'Partner','description':'New text','sector':'Technology','check_min':1000000,'check_max':5000000,'currency':'USD'}]}
def test_derive_company_website_without_personal_domains():
 assert website_candidate(profile())[0]=='https://01a.com'
 p=profile();p['contacts'][0]['email']='person@gmail.com'
 with pytest.raises(HTTPException):website_candidate(p)
def test_fills_missing_preserves_existing():
 e=automatic_edit(profile(),proposal())
 assert e.organization.description=='Original description'
 assert e.contacts[0].title=='Original title'
 assert e.contacts[0].phone=='+1 212 555 0100'
 assert e.organization.sector_focus=='Technology'
 assert e.mandates[0].criteria.check_min==1000000
 assert e.research_reviewed
 assert e.mandates[0].sources==['https://01a.com/about']
def test_mismatched_firm_never_populates():
 p=proposal();p['records'][0]['firm_name']='Different Firm'
 with pytest.raises(ValueError):automatic_edit(profile(),p)
def test_preserves_existing_contact_when_adding_published_colleague():
 p=proposal();p['records'][0].update(first_name='Other',last_name='Person',email='other@01a.com')
 e=automatic_edit(profile(),p)
 assert len(e.contacts)==2 and e.contacts[0].phone is None
 assert e.contacts[0].email=='bocci@01a.com'
def test_multiple_strategies_not_silently_broadened():
 p=profile();p['mandates']=[{'id':None,'name':n,'criteria':{'sectors':['Healthcare']},'evidence_status':'unverified','sources':[],'active':True} for n in ['A','B']]
 e=automatic_edit(p,proposal())
 assert all(m.criteria.sectors==['Healthcare'] for m in e.mandates)
 assert 'Multiple investment strategies' in e.research_summary

def test_import_csv_mapping_and_reject_missing_firm():
 import base64
 from app.investor_import import parse_rows
 encode=lambda s:base64.b64encode(s.encode()).decode()
 _,rows=parse_rows('investors.csv',encode('Company,Contact,Email,Website,Sector\nExample Fund,Jane Smith,jane@example.com,example.com,Healthcare\n'))
 assert rows[0]['firm_name']=='Example Fund'
 assert rows[0]['sector_focus']=='Healthcare'
 with pytest.raises(HTTPException):parse_rows('bad.csv',encode('Email\na@example.com'))
