-- Additive CRM expansion. Run once through the migration owner, never at app startup.
alter table investor_organizations add column if not exists website_url text;
alter table investor_organizations add column if not exists linkedin_url text;
alter table investor_organizations add column if not exists firm_type text;
alter table investor_organizations add column if not exists description text;
alter table investor_organizations add column if not exists headquarters text;
alter table investor_organizations add column if not exists relationship_status text not null default 'unknown';
alter table investor_organizations add column if not exists discovery_status text not null default 'existing_record';
alter table investor_organizations add column if not exists enrichment_status text not null default 'not_researched';
alter table investor_organizations add column if not exists last_researched_at timestamptz;
alter table investor_organizations add column if not exists updated_at timestamptz not null default now();
alter table investor_contacts add column if not exists linkedin_url text;
alter table investor_contacts add column if not exists active boolean not null default true;
alter table deals add column if not exists funding_profile jsonb not null default '{}'::jsonb;

-- Tenant-scoped composite keys prevent a child from linking to another tenant's parent.
create unique index if not exists ux_crm_org_tenant_id on investor_organizations(tenant_id,id);
create unique index if not exists ux_crm_deal_tenant_id on deals(tenant_id,id);

create table if not exists investor_mandates(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid not null,
 name text not null default 'Investment criteria',
 criteria jsonb not null default '{}'::jsonb check(jsonb_typeof(criteria)='object'),
 evidence_status text not null check(evidence_status in ('banker_verified','source_stated','inferred','unverified')),
 sources jsonb not null default '[]'::jsonb check(jsonb_typeof(sources)='array'),
 active boolean not null default true,
 verified_at timestamptz,
 updated_at timestamptz not null default now(),
 foreign key(tenant_id,organization_id) references investor_organizations(tenant_id,id)
);
create index if not exists ix_mandate_org on investor_mandates(tenant_id,organization_id);
create index if not exists ix_mandate_criteria on investor_mandates using gin(criteria);

create table if not exists investor_evidence(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid not null,
 field_name text not null,
 value jsonb not null,
 source_url text not null,
 source_title text,
 evidence_summary text,
 status text not null check(status in ('source_stated','historical_activity','inferred','banker_verified','rejected')),
 retrieved_at timestamptz not null default now(),
 reviewed_by uuid references users(id),
 foreign key(tenant_id,organization_id) references investor_organizations(tenant_id,id)
);

create table if not exists crm_source_records(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid,
 source_key text not null,
 filename text not null,
 sheet_name text,
 row_number integer,
 source_url text,
 file_sha256 text,
 raw_record jsonb not null,
 normalized_record jsonb not null default '{}'::jsonb,
 status text not null default 'needs_review',
 imported_at timestamptz not null default now(),
 unique(tenant_id,source_key),
 foreign key(tenant_id,organization_id) references investor_organizations(tenant_id,id)
);
create index if not exists ix_crm_source_org on crm_source_records(tenant_id,organization_id);

create table if not exists crm_saved_searches(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id),
 name text not null,
 filters jsonb not null,
 created_at timestamptz not null default now()
);
create table if not exists crm_tasks(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid,
 deal_id uuid,
 assigned_to uuid references users(id),
 title text not null,
 due_date date,
 status text not null default 'open' check(status in ('open','done','cancelled')),
 created_at timestamptz not null default now(),
 foreign key(tenant_id,organization_id) references investor_organizations(tenant_id,id),
 foreign key(tenant_id,deal_id) references deals(tenant_id,id)
);

do $$
declare t text;
begin
 foreach t in array array['investor_mandates','investor_evidence','crm_source_records','crm_saved_searches','crm_tasks']
 loop
  execute format('alter table %I enable row level security',t);
  execute format('alter table %I force row level security',t);
  execute format('create policy tenant_isolation on %I using (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) with check (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  execute format('create policy internal_team_only on %I as restrictive using (current_setting(''app.user_role'',true) in (''admin'',''banker'',''deal_manager'',''compliance'')) with check (current_setting(''app.user_role'',true) in (''admin'',''banker'',''deal_manager'',''compliance''))',t);
 end loop;
end $$;
grant select,insert,update on investor_mandates,investor_evidence,crm_source_records,crm_saved_searches,crm_tasks to dealos_app;
