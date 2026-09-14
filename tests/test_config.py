import os, pytest
from app.config import Settings

def test_production_rejects_dev_auth():
    with pytest.raises(ValueError):
        Settings(app_env="production",dev_auth_bypass=True,cookie_secure=True,
                 oidc_issuer="https://example.invalid",oidc_client_id="x",oidc_client_secret="x",
                 azure_storage_account_url="https://x.blob.core.windows.net")

def test_production_requires_secure_cookie():
    with pytest.raises(ValueError):
        Settings(app_env="production",dev_auth_bypass=False,cookie_secure=False,
                 oidc_issuer="https://example.invalid",oidc_client_id="x",oidc_client_secret="x",
                 azure_storage_account_url="https://x.blob.core.windows.net")

def test_production_requires_malware_scan():
    with pytest.raises(ValueError):
        Settings(app_env="production",dev_auth_bypass=False,cookie_secure=True,malware_scan_required=False,
                 oidc_issuer="https://example.invalid",oidc_client_id="x",oidc_client_secret="x",
                 azure_storage_account_url="https://x.blob.core.windows.net")
