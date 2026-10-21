create table if not exists crm_relationship_teams(
 tenant_id uuid not null, entity_kind text not null check(entity_kind in ('prospect','investor','deal')), entity_id uuid not null,
 banker_ids uuid[] not null default '{}', manager_user_id uuid, updated_at timestamptz not null default now(),
 primary key(tenant_id,entity_kind,entity_id),
 foreign key(tenant_id,manager_user_id) references users(tenant_id,id));
alter table crm_relationship_teams enable row level security;
alter table crm_relationship_teams force row level security;
drop policy if exists tenant_team on crm_relationship_teams;
create policy tenant_team on crm_relationship_teams using(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance')) with check(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','banker','deal_manager'));
grant select,insert,update on crm_relationship_teams to dealos_app;
