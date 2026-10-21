-- Additive follow-up migration: apply separately from the immutable initial schema.
-- Only a verified, pre-provisioned identity can discover its own user before
-- tenant context is known. All write policies and forced tenant RLS stay in place.
create policy provisioned_identity_login on public.users for select to dealos_app
using (exists (
  select 1 from public.auth_identities a
  where a.user_id = users.id
    and a.issuer = nullif(current_setting('app.auth_issuer', true), '')
    and a.subject = nullif(current_setting('app.auth_subject', true), '')
));

-- Provisioning belongs to the administrator, never the web application's role.
revoke insert, update, delete on public.auth_identities from dealos_app;
grant update(last_login_at) on public.auth_identities to dealos_app;
