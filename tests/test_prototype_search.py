from app.prototype_search import InvestorSearch,match_criteria

def test_selected_fields_use_and_with_or_within_category():
    s=InvestorSearch(selections={'category':['Private Equity','Family Office'],'stage':['Growth']})
    assert match_criteria({'firm_type':'Family Office'},{'stages':['Growth']},s)
    assert match_criteria({'firm_type':'Family Office'},{'stages':['Seed']},s) is None

def test_unknown_never_overrides_explicit_conflict():
    s=InvestorSearch(unknown=True,selections={'stage':['Growth']})
    assert match_criteria({}, {},s)['missing']
    assert match_criteria({}, {'stages':['Seed']},s) is None

def test_sector_parent_matches_child_but_child_requires_evidence():
    assert match_criteria({}, {'sectors':['Technology / Software & SaaS Products']},InvestorSearch(selections={'sector':['Technology']}))
    assert match_criteria({}, {'sectors':['Technology']},InvestorSearch(selections={'subsector':['Technology / Software & SaaS Products']})) is None

def test_exclusion_overrides_inclusion():
    assert match_criteria({}, {'sectors':['Technology'],'excluded_sectors':['Technology']},InvestorSearch(selections={'sector':['Technology']})) is None

def test_amount_overlap_and_currency():
    criteria={'currency':'USD','check_min':1000000,'check_max':5000000}
    assert match_criteria({},criteria,InvestorSearch(minimum=2000000,maximum=3000000))
    assert match_criteria({},criteria,InvestorSearch(minimum=6000000)) is None
    assert match_criteria({},criteria,InvestorSearch(minimum=2000000,currency='EUR')) is None

def test_partial_range_remains_uncertain():
    assert match_criteria({}, {'currency':'USD','check_min':100},InvestorSearch(minimum=200)) is None
    assert match_criteria({}, {'currency':'USD','check_min':100},InvestorSearch(minimum=200,unknown=True))['missing']

def test_empty_search_returns_records_without_criteria():
    assert match_criteria({}, {},InvestorSearch())=={'missing':[],'reasons':[]}
