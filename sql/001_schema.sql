create extension if not exists pgcrypto;
create extension if not exists citext;

create table if not exists tenants(
 id uuid primary key default gen_random_uuid(),
 name text not null,
 slug text not null unique,
 created_at timestamptz not null default now()
);

create table if not exists users(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 email citext not null,
 display_name text not null,
 role text not null check(role in ('admin','banker','deal_manager','compliance','investor')),
 investor_contact_id uuid,
 active boolean not null default true,
 created_at timestamptz not null default now(),
 unique(tenant_id,email)
);

create table if not exists auth_identities(
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references users(id) on delete cascade,
 issuer text not null,
 subject text not null,
 email text,
 last_login_at timestamptz,
 unique(issuer,subject)
);

create table if not exists investor_organizations(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 name text not null,
 domain text,
 sector_focus text,
 geography text,
 relationship_owner text,
 source text,
 notes text,
 created_at timestamptz not null default now()
);
create index if not exists ix_investor_org_tenant_name on investor_organizations(tenant_id,name);

create table if not exists investor_contacts(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid not null references investor_organizations(id) on delete cascade,
 name text,title text,email citext,phone text,
 verified boolean not null default false,
 generic_email boolean not null default false,
 source text,last_contact_at timestamptz,
 created_at timestamptz not null default now(),
 unique(tenant_id,email)
);
alter table users drop constraint if exists users_investor_contact_id_fkey;
alter table users add constraint users_investor_contact_id_fkey foreign key(investor_contact_id) references investor_contacts(id);

create table if not exists deals(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 name text not null,
 mandate_type text,
 stage text not null default 'pre_sourcing',
 owner_user_id uuid references users(id),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);

create table if not exists deal_investors(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 deal_id uuid not null references deals(id) on delete cascade,
 organization_id uuid not null references investor_organizations(id) on delete cascade,
 access_level smallint not null default 0 check(access_level between 0 and 5),
 match_score numeric(5,2),
 status text,
 revoked_at timestamptz,
 updated_at timestamptz not null default now(),
 unique(deal_id,organization_id)
);

create table if not exists interactions(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 organization_id uuid not null references investor_organizations(id),
 contact_id uuid references investor_contacts(id),
 deal_id uuid references deals(id),
 interaction_type text not null,
 occurred_at timestamptz not null default now(),
 summary text,
 created_by uuid references users(id),
 created_at timestamptz not null default now()
);

create table if not exists documents(
 id uuid primary key,
 tenant_id uuid not null references tenants(id),
 deal_id uuid not null references deals(id) on delete cascade,
 filename text not null,
 storage_key text not null unique,
 mime_type text,size_bytes bigint not null,
 sha256 char(64) not null,
 confidentiality_level smallint not null check(confidentiality_level between 1 and 5),
 watermark_required boolean not null default false,
 scan_status text not null check(scan_status in ('pending','clean','malicious','scan_error')),
 scan_finished_at timestamptz,
 uploaded_by uuid not null references users(id),
 created_at timestamptz not null default now()
);

create table if not exists audit_events(
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 user_id uuid,
 event_type text not null,
 object_type text,
 object_id text,
 detail jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);
create index if not exists ix_audit_tenant_time on audit_events(tenant_id,created_at desc);

create or replace function audit_event(p_user text,p_event text,p_type text,p_id text,p_detail jsonb)
returns void language plpgsql security invoker as $$
begin
 insert into audit_events(tenant_id,user_id,event_type,object_type,object_id,detail)
 values(current_setting('app.tenant_id')::uuid,nullif(p_user,'')::uuid,p_event,p_type,p_id,coalesce(p_detail,'{}'::jsonb));
end $$;

create or replace function audit_immutable() returns trigger language plpgsql as $$
begin raise exception 'audit_events are immutable'; end $$;
drop trigger if exists trg_audit_immutable on audit_events;
create trigger trg_audit_immutable before update or delete on audit_events
for each row execute function audit_immutable();

-- RLS: application role must NOT have BYPASSRLS and must not own these tables.
do $$
declare t text;
begin
 foreach t in array array['users','investor_organizations','investor_contacts','deals','deal_investors','interactions','documents','audit_events']
 loop
   execute format('alter table %I enable row level security',t);
   execute format('alter table %I force row level security',t);
   execute format('drop policy if exists tenant_isolation on %I',t);
   execute format($p$create policy tenant_isolation on %I
     using (tenant_id = nullif(current_setting('app.tenant_id',true),'')::uuid)
     with check (tenant_id = nullif(current_setting('app.tenant_id',true),'')::uuid)$p$,t);
 end loop;
end $$;

-- Extra defense: investors cannot enumerate investor CRM or audit data through direct SQL.
drop policy if exists investor_org_internal_only on investor_organizations;
create policy investor_org_internal_only on investor_organizations as restrictive
using (current_setting('app.user_role',true) <> 'investor')
with check (current_setting('app.user_role',true) <> 'investor');

drop policy if exists investor_contacts_internal_only on investor_contacts;
create policy investor_contacts_internal_only on investor_contacts as restrictive
using (
 current_setting('app.user_role',true) <> 'investor'
 or id = nullif(current_setting('app.investor_contact_id',true),'')::uuid
)
with check (current_setting('app.user_role',true) <> 'investor');

drop policy if exists audit_internal_only on audit_events;
create policy audit_internal_only on audit_events as restrictive
using (current_setting('app.user_role',true) in ('admin','compliance','deal_manager'))
with check (true);
