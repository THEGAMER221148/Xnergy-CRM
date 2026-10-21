import pytest
from fastapi import HTTPException
from app import ask

@pytest.mark.asyncio
async def test_question_requires_authorized_internal_role(monkeypatch):
    async def deny(request,roles):
        assert 'investor' not in roles
        raise HTTPException(403)
    async def unexpected(*args):raise AssertionError('AI must not run before authorization')
    monkeypatch.setattr(ask,'require_user',deny)
    monkeypatch.setattr(ask,'answer_question',unexpected)
    with pytest.raises(HTTPException) as e:await ask.ask(ask.Question(question='What is the process?'),None)
    assert e.value.status_code==403

@pytest.mark.asyncio
async def test_question_requires_csrf_before_ai(monkeypatch):
    async def user(*args):return {'user_id':'test','tenant_id':'test'}
    async def deny(*args):raise HTTPException(403)
    async def unexpected(*args):raise AssertionError('AI must not run without CSRF')
    monkeypatch.setattr(ask,'require_user',user)
    monkeypatch.setattr(ask,'enforce_csrf',deny)
    monkeypatch.setattr(ask,'answer_question',unexpected)
    with pytest.raises(HTTPException) as e:await ask.ask(ask.Question(question='What is the process?'),None)
    assert e.value.status_code==403

def test_knowledge_has_numbered_source_pages():
    assert len(ask.PAGES)>1
    assert len({p['page'] for p in ask.PAGES})==len(ask.PAGES)
    assert all(p['text'].strip() for p in ask.PAGES)
