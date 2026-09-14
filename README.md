# Xnergy DealOS — Production Security Build

This package replaces the local MVP security/database/storage layers with production-oriented controls:

- **OIDC/SSO**: Authorization Code + PKCE, no production password database.
- **Pre-provisioned identities**: investors must already exist in the CRM/user model before SSO can attach.
- **Persistent sessions**: Redis-backed opaque server sessions.
- **PostgreSQL**: tenant-aware schema with forced Row-Level Security.
- **Native secure data room**: private Azure Blob containers.
- **Malware quarantine**: files remain inaccessible until Microsoft Defender for Storage clears them.
- **Rate limiting + CSRF + security headers**.
- **Append-only audit trail**.
- **Azure IaC**: VNet, private PostgreSQL, HA/backups, private storage posture, Key Vault, Redis, Service Bus, Container Apps, logging.
- **Security CI**: tests, Bandit, pip-audit, and Trivy.

## Important deployment boundary

The code is deployable, but no artifact can truthfully be called production-secure until it is configured in **your** Entra tenant/Azure subscription, DNS/TLS is active, secrets are placed in Key Vault, Defender scanning is wired to the Service Bus queue, and the production security gates in `SECURITY.md` pass.

## Local integration run

1. Copy `.env.example` to `.env`.
2. For Azurite, set `AZURE_STORAGE_CONNECTION_STRING` to Azurite's standard development connection string.
3. `docker compose up --build`
4. Open `http://127.0.0.1:8000/auth/login`.

`DEV_AUTH_BYPASS=true` is allowed only outside production; the configuration class refuses to start a production instance with it enabled.

## Production authentication

Register a confidential OIDC web app with redirect URI:

`https://YOUR_DOMAIN/auth/callback`

Populate:
- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`

For Xnergy, Microsoft Entra ID is the natural internal provider. The same OIDC implementation can support other banks' providers later.

## Database

Run migrations as a migration/owner identity, then connect the web application using the non-owner `dealos_app` runtime role.

The RLS context is set transaction-locally on every request:
- `app.tenant_id`
- `app.user_id`
- `app.user_role`
- `app.investor_contact_id`

## Data room malware flow

`upload -> private quarantine container -> Defender scan -> Event Grid -> Service Bus -> malware worker -> clean container`

Only documents whose DB status is `clean` are downloadable.

## Next configuration inputs needed from Xnergy

- Azure subscription/resource group policy
- Entra tenant ID
- production hostname
- OIDC application registration credentials
- preferred Azure region/data residency
- security/compliance retention requirements
- GitHub repo URL/branch and deployment environment naming
