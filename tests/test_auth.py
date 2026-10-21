from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main, security


def test_entra_identity_uses_directory_id_not_email_or_pairwise_subject():
    tenant = '11111111-1111-4111-8111-111111111111'
    oid = '22222222-2222-4222-8222-222222222222'
    claims = {'iss': f'https://login.microsoftonline.com/{tenant}/v2.0', 'tid': tenant, 'oid': oid, 'sub': 'pairwise', 'email': 'untrusted@example.invalid'}
    assert security.identity_key(claims) == (claims['iss'], 'entra-oid:' + oid)
    claims['sub'] = 'another-pairwise-value'
    assert security.identity_key(claims)[1] == 'entra-oid:' + oid


@pytest.mark.parametrize('override', [{'oid': ''}, {'oid': 'not-a-guid'}, {'tid': '33333333-3333-4333-8333-333333333333'}, {'tid': None}])
def test_entra_identity_rejects_missing_or_mismatched_claims(override):
    tenant = '11111111-1111-4111-8111-111111111111'
    claims = {'iss': f'https://login.microsoftonline.com/{tenant}/v2.0', 'tid': tenant, 'oid': '22222222-2222-4222-8222-222222222222', 'sub': 'pairwise'}
    with pytest.raises(HTTPException):
        security.identity_key({**claims, **override})


def test_generic_provider_uses_subject():
    claims = {'iss': 'https://issuer.invalid', 'sub': 'subject', 'oid': 'untrusted'}
    assert security.identity_key(claims) == (claims['iss'], 'subject')


@pytest.mark.parametrize("path", ["https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/%2fevil.invalid", "/%5cevil.invalid", "/\nevil.invalid"])
def test_redirect_rejects_external_or_ambiguous_paths(path):
    assert security.safe_next_path(path) == "/"


def test_redirect_preserves_local_destination():
    assert security.safe_next_path("/deals?stage=active") == "/deals?stage=active"


@pytest.mark.asyncio
async def test_callback_uses_provisioned_identity_and_secure_cookies(monkeypatch):
    from contextlib import asynccontextmanager
    claims = {"iss": "https://issuer.invalid", "sub": "approved-subject"}
    user = {"id": "approved-user"}
    connection = object()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    resolver = AsyncMock(return_value=user)
    session = AsyncMock(return_value=("session", "csrf"))
    monkeypatch.setattr(main, "pool", lambda: Pool())
    monkeypatch.setattr(main, "oidc_callback", AsyncMock(return_value=(claims, "//evil.invalid")))
    monkeypatch.setattr(security, "resolve_identity", resolver)
    monkeypatch.setattr(main, "create_session", session)
    response = await main.callback("code", "state")
    resolver.assert_awaited_once_with(connection, claims)
    session.assert_awaited_once_with(user)
    assert response.headers["location"] == "/"
    cookies = response.headers.getlist("set-cookie")
    assert "HttpOnly" in cookies[0] and "Secure" in cookies[0]


@pytest.mark.asyncio
async def test_callback_does_not_create_session_for_unknown_identity(monkeypatch):
    from contextlib import asynccontextmanager

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield object()

    session = AsyncMock()
    monkeypatch.setattr(main, "pool", lambda: Pool())
    monkeypatch.setattr(main, "oidc_callback", AsyncMock(return_value=({"iss": "issuer", "sub": "unknown"}, "/")))
    monkeypatch.setattr(security, "resolve_identity", AsyncMock(side_effect=HTTPException(403)))
    monkeypatch.setattr(main, "create_session", session)
    with pytest.raises(HTTPException) as error:
        await main.callback("code", "state")
    assert error.value.status_code == 403
    session.assert_not_awaited()


def test_health_uses_current_redis_and_rate_limiter(monkeypatch):
    from contextlib import asynccontextmanager

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield AsyncMock()

    redis = AsyncMock()
    limiter = AsyncMock()
    monkeypatch.setattr(main, "pool", lambda: Pool())
    monkeypatch.setattr(security, "redis_client", redis)
    monkeypatch.setattr(main, "rate_limit", limiter)
    client = TestClient(main.app)
    assert client.get("/readyz").status_code == 200
    redis.ping.assert_awaited_once()
    limiter.assert_awaited_once()
    redis.ping.side_effect = ConnectionError()
    assert client.get("/readyz").status_code == 503
