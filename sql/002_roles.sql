-- Run through scripts/migrate.py after schema migration.
-- The runner enables login and sets the supplied password in the same transaction.
-- The runtime role is intentionally non-owner and NOBYPASSRLS.
do $$
begin
 if not exists(select 1 from pg_roles where rolname='dealos_app') then
   create role dealos_app nologin;
 end if;
end $$;
-- New roles default to NOSUPERUSER and NOBYPASSRLS; the runner verifies both.
-- Azure administrators cannot issue ALTER ROLE for superuser-only attributes.
alter role dealos_app nocreatedb nocreaterole noinherit;
grant connect on database dealos to dealos_app;
grant usage on schema public to dealos_app;
grant select,insert,update,delete on users,auth_identities,investor_organizations,investor_contacts,deals,deal_investors,interactions,documents to dealos_app;
grant select,insert on audit_events to dealos_app;
grant execute on function audit_event(text,text,text,text,jsonb) to dealos_app;
