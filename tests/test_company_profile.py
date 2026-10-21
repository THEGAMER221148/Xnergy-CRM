import pytest
from pydantic import ValidationError
from app.crm import CompanyProfile,Prospect
from app.intake import extraction_schema

def test_company_capital_is_not_investor_check_size():
    p=Prospect(name='Example company',service_need='capital_raise',company_profile={'capital_sought':5000000,'annual_revenue':22000000,'currency':'USD','ownership':'Family-owned'})
    assert p.company_profile.capital_sought==5000000
    assert 'check_min' not in p.company_profile.model_dump()

def test_negative_ebitda_allowed_but_negative_capital_rejected():
    assert CompanyProfile(ebitda=-300000).ebitda==-300000
    with pytest.raises(ValidationError):CompanyProfile(capital_sought=-1)
    with pytest.raises(ValidationError):CompanyProfile(annual_revenue=float('inf'))

def test_ai_schema_requires_explicit_nulls_for_unknown_company_fields():
    schema=extraction_schema()['$defs']['Draft']
    assert set(schema['properties'])==set(schema['required'])
    assert 'capital_sought' in schema['properties']
    assert 'default' not in schema['properties']['capital_sought']
