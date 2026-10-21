import pytest
from app import ask

@pytest.mark.asyncio
async def test_company_answer_returns_named_sources_without_full_text(monkeypatch):
    async def user(*args): return {'user_id':'u','tenant_id':'t'}
    async def noop(*args): pass
    async def answer(*args): return ask.Answer(answer='Xnergy provides corporate finance and M&A advisory.',source_pages=[1001,1003],supported=True)
    monkeypatch.setattr(ask,'require_user',user)
    monkeypatch.setattr(ask,'enforce_csrf',noop)
    monkeypatch.setattr(ask,'rate_limit',noop)
    monkeypatch.setattr(ask,'document_plan',noop)
    monkeypatch.setattr(ask,'answer_question',answer)
    result=await ask.ask(ask.Question(question='What services do we offer?'),None)
    assert [s['title'] for s in result['sources']]==['Company overview and pitch','Products and services']
    assert all(s['url'].startswith('https://xnergy.com/') for s in result['sources'])
    assert all('text' not in s for s in result['sources'])
