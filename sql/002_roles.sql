-- Run as database administrator after schema migration.
-- The runtime role is intentionally non-owner and NOBYPASSRLS.
do $$
begin
 if not exists(select 1 from pg_roles where rolname='dealos_app') then
   create role dealos_app login password 'CHANGE_ME';
 end if;
end $$;
alter role dealos_app nobypassrls nosuperuser nocreatedb nocreaterole noinherit;
grant connect on database dealos to dealos_app;
grant usage on schema public to dealos_app;
grant select,insert,update,delete on users,auth_identities,investor_organizations,investor_contacts,deals,deal_investors,interactions,documents to dealos_app;
grant select,insert on audit_events to dealos_app;
grant execute on function audit_event(text,text,text,text,jsonb) to dealos_app;
