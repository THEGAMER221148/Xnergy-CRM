-- Additive migration; must follow 004. No source data is removed or reclassified.
create unique index if not exists ux_users_tenant_id on users(tenant_id,id);
create table if not exists company_prospects(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 name text not null,
 website_url text,
 sector text,
 geography text,
 service_need text not null default 'unknown' check(service_need in ('unknown','capital_raise','business_sale','both')),
 status text not null default 'unqualified' check(status in ('unqualified','researching','contacted','qualified','not_a_fit','client')),
 owner_user_id uuid,
 notes text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 foreign key(tenant_id,owner_user_id) references users(tenant_id,id)
);
create index if not exists ix_prospect_tenant_name on company_prospects(tenant_id,name);
alter table company_prospects enable row level security;
alter table company_prospects force row level security;
drop policy if exists tenant_isolation on company_prospects;
create policy tenant_isolation on company_prospects
 using (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 with check (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
drop policy if exists internal_team_only on company_prospects;
create policy internal_team_only on company_prospects as restrictive
 using (current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance'))
 with check (current_setting('app.user_role',true) in ('admin','banker','deal_manager'));
grant select,insert,update on company_prospects to dealos_app;
create unique index if not exists ux_prospect_tenant_id on company_prospects(tenant_id,id);
alter table crm_tasks add column if not exists prospect_id uuid;
alter table crm_tasks add constraint crm_task_prospect_tenant_fk foreign key(tenant_id,prospect_id) references company_prospects(tenant_id,id);
alter table crm_tasks add constraint crm_task_assignee_tenant_fk foreign key(tenant_id,assigned_to) references users(tenant_id,id);
alter table crm_saved_searches add constraint crm_search_creator_tenant_fk foreign key(tenant_id,created_by) references users(tenant_id,id);
alter table investor_evidence add constraint crm_evidence_reviewer_tenant_fk foreign key(tenant_id,reviewed_by) references users(tenant_id,id);
