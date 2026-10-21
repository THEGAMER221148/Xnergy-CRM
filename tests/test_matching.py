import pytest
from app.matching import match_mandate, rank_investors


def test_unknown_is_not_full_match():
    result = match_mandate({'sector': 'Healthcare'}, {})
    assert result['score'] == result['coverage'] == 0
    assert result['status'] == 'needs_verification'


def test_round_size_does_not_exclude_smaller_coinvestor():
    r = match_mandate({'funding_amount': 100000000, 'currency': 'USD'},
                     {'check_min': 1000000, 'check_max': 10000000, 'currency': 'USD'})
    assert r['eligible']
    assert next(c for c in r['criteria'] if c['criterion'] == 'check_size')['status'] == 'unknown'


def test_known_mismatch_excluded_and_boundary_included():
    d = {'sector': 'Healthcare', 'ticket_min': 10000000, 'ticket_max': 10000000, 'currency': 'USD'}
    m = {'sectors': ['healthcare'], 'check_min': 1000000, 'check_max': 10000000, 'currency': 'USD'}
    assert match_mandate(d, m)['eligible']
    assert not match_mandate(dict(d, ticket_min=10000001, ticket_max=10000001), m)['eligible']
    assert not match_mandate(dict(d, sector='Energy'), m)['eligible']


def test_currency_mismatch_requires_verification():
    r = match_mandate({'ticket_min': 20, 'ticket_max': 30, 'currency': 'EUR'},
                     {'check_min': 1, 'check_max': 10, 'currency': 'USD'})
    assert r['eligible'] and r['score'] == 0


def test_explicit_exclusion_wins_over_unrestricted():
    assert not match_mandate({'sector': 'Tobacco'}, {'sectors': ['*'], 'excluded_sectors': ['tobacco']})['eligible']


def test_multiple_mandates_choose_supported_fit():
    results = rank_investors({'sector': 'healthcare'}, [
        {'id': 'a', 'name': 'Unknown', 'mandates': []},
        {'id': 'b', 'name': 'Known', 'mandates': [{'sectors': ['energy']}, {'sectors': ['healthcare']}]},
        {'id': 'c', 'name': 'Excluded', 'mandates': [{'sectors': ['energy']}]},
    ])
    assert [r['investor_id'] for r in results] == ['b', 'a']
    assert results[0]['score'] == 25


def test_invalid_ranges_and_nonfinite_values_rejected():
    with pytest.raises(ValueError):
        match_mandate({'ticket_min': 10, 'ticket_max': 5}, {})
    with pytest.raises(ValueError):
        match_mandate({'revenue': 'NaN'}, {})
