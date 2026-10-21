"""Explainable mandate matching. Unknown facts never count as a match."""
from decimal import Decimal, InvalidOperation
import re

WEIGHTS = {'sectors': 25, 'geographies': 20, 'check_size': 20,
           'financing_types': 10, 'stages': 5, 'transaction_types': 5,
           'ownership_preferences': 5, 'revenue': 5, 'ebitda': 5}


def term(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().casefold())


def number(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Amounts must be numbers') from None
    if not result.is_finite():
        raise ValueError('Amounts must be finite')
    return result


def bounds(record, prefix):
    low, high = number(record.get(prefix + '_min')), number(record.get(prefix + '_max'))
    if low is not None and high is not None and low > high:
        raise ValueError('Minimum exceeds maximum: ' + prefix)
    return low, high


def match_mandate(deal, mandate):
    """Match a single mandate, keeping exclusions and coverage separate from score.

    Amounts are in whole currency units. Check size uses requested investor ticket,
    never the total financing round. A missing mandate field means unknown, not any.
    A mandate can explicitly contain '*' to state an unrestricted category.
    """
    criteria = []
    mappings = [('sectors', 'sector'), ('geographies', 'geography'),
                ('financing_types', 'financing_type'), ('stages', 'stage'),
                ('transaction_types', 'transaction_type'),
                ('ownership_preferences', 'ownership_preference')]
    excluded_sectors = {term(x) for x in mandate.get('excluded_sectors', [])}
    excluded_geographies = {term(x) for x in mandate.get('excluded_geographies', [])}
    exclusions = []
    for key, excluded in [('sector', excluded_sectors), ('geography', excluded_geographies)]:
        if term(deal.get(key)) and term(deal[key]) in excluded:
            exclusions.append('Explicitly excluded ' + key + ': ' + str(deal[key]))
    for key, deal_key in mappings:
        actual = term(deal.get(deal_key))
        accepted = {term(x) for x in mandate.get(key, []) if term(x)}
        state = 'unknown' if not actual or not accepted else ('match' if actual in accepted or '*' in accepted else 'mismatch')
        criteria.append({'criterion': key, 'status': state,
                         'reason': 'Not enough stated criteria' if state == 'unknown' else
                         ('Within stated mandate' if state == 'match' else 'Outside stated mandate'),
                         'deal_value': deal.get(deal_key), 'investor_values': sorted(accepted)})
    for key, deal_prefix, mandate_prefix in [('check_size', 'ticket', 'check'),
                                              ('revenue', 'revenue', 'revenue'),
                                              ('ebitda', 'ebitda', 'ebitda')]:
        low, high = bounds(mandate, mandate_prefix)
        if key == 'check_size':
            actual_low, actual_high = bounds(deal, deal_prefix)
        else:
            actual_low = actual_high = number(deal.get(deal_prefix))
        currency_known = bool(deal.get('currency')) and bool(mandate.get('currency'))
        same_currency = currency_known and term(deal['currency']) == term(mandate['currency'])
        if not same_currency:
            status, reason = 'unknown', 'Currency missing or conversion required'
        elif (low is None and high is None) or (actual_low is None and actual_high is None):
            status, reason = 'unknown', 'Amount or stated investment range missing'
        elif (high is not None and actual_low is not None and actual_low > high) or (low is not None and actual_high is not None and actual_high < low):
            status, reason = 'mismatch', 'Outside stated investment range'
        else:
            status, reason = 'match', 'Overlaps stated investment range'
        criteria.append({'criterion': key, 'status': status, 'reason': reason,
                         'deal_min': str(actual_low) if actual_low is not None else None,
                         'deal_max': str(actual_high) if actual_high is not None else None,
                         'investor_min': str(low) if low is not None else None,
                         'investor_max': str(high) if high is not None else None})
    matched = sum(WEIGHTS[c['criterion']] for c in criteria if c['status'] == 'match')
    known = sum(WEIGHTS[c['criterion']] for c in criteria if c['status'] != 'unknown')
    mismatches = [c['criterion'] for c in criteria if c['status'] == 'mismatch']
    eligible = not exclusions and not mismatches and mandate.get('active', True)
    return {'eligible': bool(eligible), 'score': matched, 'coverage': known,
            'status': 'excluded' if not eligible else ('supported_match' if known == 100 else 'needs_verification'),
            'criteria': criteria, 'exclusions': exclusions, 'mismatches': mismatches,
            'mandate_id': str(mandate.get('id', '')),
            'notice': 'Fit against recorded criteria; not a prediction of investor interest.'}


def rank_investors(deal, investors, include_excluded=False):
    results = []
    for investor in investors:
        mandates = investor.get('mandates') or [{}]
        matches = [match_mandate(deal, m) for m in mandates]
        best = max(matches, key=lambda r: (r['eligible'], r['score'], r['coverage']))
        if best['eligible'] or include_excluded:
            results.append({'investor_id': str(investor['id']), 'name': investor['name'], **best})
    return sorted(results, key=lambda r: (-int(r['eligible']), -r['score'], -r['coverage'], r['name'].casefold()))
