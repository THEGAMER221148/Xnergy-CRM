create table if not exists investor_research_jobs(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid not null,
 requested_by uuid not null references users(id),
 status text not null default 'pending' check(status in ('pending','running','completed','needs_follow_up','failed')),
 attempts integer not null default 0,
 proposal jsonb,
 result_summary text,
 created_at timestamptz not null default now(),
 started_at timestamptz,
 finished_at timestamptz,
 unique(tenant_id,organization_id),
 foreign key(tenant_id,organization_id) references investor_organizations(tenant_id,id)
);
alter table investor_research_jobs enable row level security;
alter table investor_research_jobs force row level security;
do $$ begin
 if not exists(select 1 from pg_policies where tablename='investor_research_jobs' and policyname='tenant_isolation') then
 create policy tenant_isolation on investor_research_jobs using(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid) with check(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
 create policy internal_team_only on investor_research_jobs as restrictive using(current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance'));
 end if;
end $$;
grant select,insert,update on investor_research_jobs to dealos_app;
