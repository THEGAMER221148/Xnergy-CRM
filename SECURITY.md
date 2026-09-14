# Xnergy DealOS security model

## Identity
- No production password database.
- OIDC Authorization Code + PKCE.
- Users must be pre-provisioned in DealOS. An investor identity can only attach to an existing active CRM-backed user record.
- Redis-backed server sessions use random opaque IDs. Cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`.
- State-changing API calls require a separate CSRF token.

## Tenant and investor authorization
- PostgreSQL Row-Level Security is enabled and forced on tenant-scoped tables.
- Runtime DB role is `NOBYPASSRLS` and should not own application tables.
- Investor data-room access is additionally checked against CRM contact -> organization -> deal entitlement level.
- Access level 0 or `revoked_at` denies access.

## Files
- Blob containers are private; public access and storage account keys are disabled in production IaC.
- Uploads enter a quarantine container with `scan_status=pending`.
- Microsoft Defender for Storage scan events are consumed through Service Bus.
- Only `No threats found` promotes a blob to the clean container.
- Malicious uploads are deleted from quarantine and marked malicious.
- Pending/error/malicious documents are not downloadable.
- Downloads are streamed through the application after authorization; permanent public URLs are not issued.

## Audit
- Security-sensitive actions write an append-only database audit event.
- Database trigger rejects audit updates/deletes.
- Azure platform logs should flow to Log Analytics/SIEM with alerting.

## Rate limiting
- Redis-backed global/IP and per-user route limits are enabled.
- An internet deployment should also enable WAF/front-door rate limiting and bot protection.

## Backups and keys
- PostgreSQL IaC uses 35-day retention, zone-redundant HA, and geo-redundant backup.
- Blob versioning and soft-delete are enabled.
- Key Vault has soft delete + purge protection.
- Configure customer-managed storage/database encryption keys where the firm's compliance policy requires them.
- Perform restore drills; backup existence alone is not a recovery test.

## Production gates
Before handling client data:
1. Penetration test and remediation.
2. Threat model and data-flow review.
3. Dependency/SAST/container/IaC scanning in CI.
4. Restore test and incident-response exercise.
5. Entra Conditional Access/MFA policy verification.
6. Defender for Storage EICAR workflow validation.
7. RLS cross-tenant negative tests.
8. Legal/compliance review of retention, supervision, books-and-records, privacy, and data residency.
