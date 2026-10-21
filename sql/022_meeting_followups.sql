create table if not exists crm_meeting_followups (
 id uuid primary key, tenant_id uuid not null references tenants(id), meeting_key text not null,
 subject text not null, record_type text not null check(record_type in ('prospect','investor')),
 record_id uuid not null, note text not null default '', task_id uuid references crm_tasks(id),
 created_by uuid not null references users(id), payload_hash text not null, created_at timestamptz not null default now()
);
create index if not exists meeting_followups_key on crm_meeting_followups(tenant_id,meeting_key,created_at);
alter table crm_meeting_followups enable row level security;
alter table crm_meeting_followups force row level security;
drop policy if exists meeting_followups_tenant on crm_meeting_followups;
create policy meeting_followups_tenant on crm_meeting_followups using (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid) with check (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

grant select,insert on crm_meeting_followups to dealos_app;
