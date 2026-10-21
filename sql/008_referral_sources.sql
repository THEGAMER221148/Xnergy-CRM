create table referral_sources (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 firm_name text not null, first_name text, last_name text, email text, phone text,
 category text not null default 'Other', notes text,
 owner_user_id uuid, sourced_by uuid not null,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(tenant_id,id),
 foreign key(tenant_id,owner_user_id) references users(tenant_id,id),
 foreign key(tenant_id,sourced_by) references users(tenant_id,id)
);
create index referral_sources_tenant_name on referral_sources(tenant_id,firm_name);
alter table referral_sources enable row level security;
alter table referral_sources force row level security;
create policy tenant_isolation on referral_sources using(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid) with check(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
create policy internal_only on referral_sources as restrictive using(current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance')) with check(current_setting('app.user_role',true) in ('admin','banker','deal_manager'));
grant select,insert,update on referral_sources to dealos_app;
