from fastapi.testclient import TestClient
from app import main, security


def test_intake_allows_only_same_origin_embedding(monkeypatch):
    monkeypatch.setattr(security, 'redis_client', None)
    client = TestClient(main.app)
    response = client.get('/static/workspace/talk.html?embedded=1')
    assert response.status_code == 200
    assert response.headers['x-frame-options'] == 'SAMEORIGIN'
    assert "frame-ancestors 'self'" in response.headers['content-security-policy']
    assert 'microphone=(self)' in response.headers['permissions-policy']
    parent = client.get('/static/workspace/index.html')
    assert 'microphone=(self)' in parent.headers['permissions-policy']
    assert parent.headers['x-frame-options'] == 'DENY'
    other = client.get('/healthz')
    assert other.headers['x-frame-options'] == 'DENY'
    assert 'microphone=()' in other.headers['permissions-policy']
