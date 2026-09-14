from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_env: str = "development"
    app_base_url: str = "http://127.0.0.1:8000"
    database_url: str = "postgresql://dealos_app:devpass@localhost:5432/dealos"
    redis_url: str = "redis://localhost:6379/0"

    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_allowed_internal_domain: str = "xnergy.com"

    dev_auth_bypass: bool = False
    dev_auth_email: str = "admin@xnergy.local"

    session_ttl_seconds: int = 28800
    cookie_secure: bool = True
    cookie_domain: str | None = None
    csrf_header_name: str = "X-CSRF-Token"

    azure_storage_account_url: str = ""
    azure_storage_connection_string: str = ""
    azure_quarantine_container: str = "dealos-quarantine"
    azure_clean_container: str = "dealos-clean"
    max_upload_bytes: int = 100 * 1024 * 1024
    malware_scan_required: bool = True

    azure_servicebus_fqdn: str = ""
    azure_servicebus_connection_string: str = ""
    malware_scan_queue: str = "dealos-malware-results"
    key_vault_url: str = ""

    @model_validator(mode="after")
    def production_safety(self):
        if self.app_env.lower() == "production":
            if self.dev_auth_bypass:
                raise ValueError("DEV_AUTH_BYPASS must be false in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            needed = {
                "OIDC_ISSUER": self.oidc_issuer,
                "OIDC_CLIENT_ID": self.oidc_client_id,
                "OIDC_CLIENT_SECRET": self.oidc_client_secret,
                "AZURE_STORAGE_ACCOUNT_URL": self.azure_storage_account_url,
            }
            missing = [k for k,v in needed.items() if not v]
            if missing:
                raise ValueError("Missing production settings: " + ", ".join(missing))
            if not self.malware_scan_required:
                raise ValueError("MALWARE_SCAN_REQUIRED must remain true in production")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
