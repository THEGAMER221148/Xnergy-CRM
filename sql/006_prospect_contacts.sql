create table prospect_contacts(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 prospect_id uuid not null,
 name text,
 first_name text,
 last_name text,
 street_address text,
 city text,
 state_region text,
 postal_code text,
 country text,
 title text,
 email citext,
 phone text,
 linkedin_url text,
 created_at timestamptz not null default now(),
 unique(tenant_id,email),
 foreign key(tenant_id,prospect_id) references company_prospects(tenant_id,id)
);
alter table prospect_contacts enable row level security;
alter table prospect_contacts force row level security;
create policy tenant_isolation on prospect_contacts
 using (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 with check (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
create policy internal_team_only on prospect_contacts as restrictive
 using (current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance'))
 with check (current_setting('app.user_role',true) in ('admin','banker','deal_manager'));
grant select,insert,update on prospect_contacts to dealos_app;
alter table investor_organizations add column if not exists street_address text;
alter table investor_organizations add column if not exists city text;
alter table investor_organizations add column if not exists state_region text;
alter table investor_organizations add column if not exists postal_code text;
alter table investor_organizations add column if not exists country text;
alter table company_prospects add column if not exists street_address text;
alter table company_prospects add column if not exists city text;
alter table company_prospects add column if not exists state_region text;
alter table company_prospects add column if not exists postal_code text;
alter table company_prospects add column if not exists country text;
alter table investor_contacts add column if not exists street_address text;
alter table investor_contacts add column if not exists city text;
alter table investor_contacts add column if not exists state_region text;
alter table investor_contacts add column if not exists postal_code text;
alter table investor_contacts add column if not exists country text;
alter table investor_contacts add column if not exists first_name text;
alter table investor_contacts add column if not exists last_name text;
