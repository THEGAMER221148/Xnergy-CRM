alter table deals add column if not exists prospect_id uuid;
create unique index if not exists ux_deal_prospect on deals(tenant_id,prospect_id) where prospect_id is not null;
do $$ begin
 if not exists(select 1 from pg_constraint where conname='deal_prospect_tenant_fk') then
 alter table deals add constraint deal_prospect_tenant_fk foreign key(tenant_id,prospect_id) references company_prospects(tenant_id,id);
 end if;
end $$;
create table if not exists deal_stage_history(
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null,deal_id uuid not null,
 stage text not null, note text, changed_by uuid not null, created_at timestamptz not null default now(),
 foreign key(tenant_id,deal_id) references deals(tenant_id,id),foreign key(tenant_id,changed_by) references users(tenant_id,id));
alter table deal_stage_history enable row level security;
alter table deal_stage_history force row level security;
drop policy if exists tenant_team on deal_stage_history;
create policy tenant_team on deal_stage_history using(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance')) with check(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','banker','deal_manager'));
grant select,insert on deal_stage_history to dealos_app;
