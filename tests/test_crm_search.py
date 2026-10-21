from decimal import Decimal
import pytest
from app.crm_search import investor_query

def test_search_values_are_parameters_not_sql():
    attack="'; drop table users; --"
    sql,args=investor_query(q=attack,category=attack,financing=attack)
    assert attack not in sql
    assert args.count(attack)==3

def test_amount_filter_uses_investor_ticket_and_same_currency():
    sql,args=investor_query(minimum=Decimal('5000000'),maximum=Decimal('15000000'),currency='USD')
    assert 'funding_amount' not in sql
    assert "check_max" in sql and "check_min" in sql
    assert "currency" in sql and 'USD' in args
    assert 'm.active' in sql
    assert 'is not null' in sql

def test_filters_require_same_mandate():
    sql,args=investor_query(financing='Debt',stage='Growth',minimum=Decimal('10'))
    assert sql.count('from investor_mandates m')==1

def test_inverted_range_rejected():
    with pytest.raises(ValueError):investor_query(minimum=20,maximum=10)
