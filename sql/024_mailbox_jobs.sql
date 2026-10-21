create table if not exists ai_mailbox_jobs (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id), status text not null default 'running'
 check(status in ('running','paused','needs_connection','completed','completed_with_exceptions')),
 start_date date, snapshot_at timestamptz not null default now(), consent_at timestamptz not null default now(),
 instructions text not null default '', next_url text, enumerated boolean not null default false,
 retry_after timestamptz not null default now(), last_error text not null default '',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create unique index if not exists mailbox_one_active on ai_mailbox_jobs(tenant_id,created_by)
 where status in ('running','paused','needs_connection');
create table if not exists ai_mailbox_items (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id), job_id uuid not null references ai_mailbox_jobs(id),
 message_id text not null, source_received_at timestamptz, source_url text not null default '',
 status text not null default 'pending' check(status in ('pending','done','exception')),
 part integer not null default 0, attempts integer not null default 0,
 results jsonb not null default '[]', exceptions jsonb not null default '[]',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(tenant_id,created_by,message_id)
);
create index if not exists mailbox_item_queue on ai_mailbox_items(job_id,status,created_at);
do $$ declare t text; begin
 foreach t in array array['ai_mailbox_jobs','ai_mailbox_items'] loop
 execute format('alter table %I enable row level security',t);
 execute format('alter table %I force row level security',t);
 if not exists(select 1 from pg_policies where tablename=t and policyname='tenant_only') then
 execute format('create policy tenant_only on %I using (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) with check (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
 execute format('create policy author_admin on %I as restrictive using (created_by=nullif(current_setting(''app.user_id'',true),'''')::uuid or current_setting(''app.user_role'',true)=''admin'') with check (created_by=nullif(current_setting(''app.user_id'',true),'''')::uuid or current_setting(''app.user_role'',true)=''admin'')',t);
 end if;
 end loop;
end $$;
grant select,insert,update on ai_mailbox_jobs,ai_mailbox_items to dealos_app;
