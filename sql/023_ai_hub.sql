create table if not exists ai_import_batches (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id), instructions text not null default '',
 sources jsonb not null default '[]', drafts jsonb not null default '[]', results jsonb not null default '[]',
 status text not null default 'preview' check(status in ('preview','imported')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists ai_feature_requests (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id), title text not null, description text not null,
 status text not null default 'submitted' check(status in ('submitted','approved','changes_requested','rejected','building','testing','deployed')),
 admin_note text not null default '', reviewed_by uuid references users(id),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists ai_work_conversations (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants(id),
 created_by uuid not null references users(id), messages jsonb not null default '[]',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
do $$ declare t text; begin
 foreach t in array array['ai_import_batches','ai_feature_requests','ai_work_conversations'] loop
 execute format('alter table %I enable row level security',t);
 execute format('alter table %I force row level security',t);
 execute format('create policy tenant_only on %I using (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) with check (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
 execute format('create policy author_admin on %I as restrictive using (created_by=nullif(current_setting(''app.user_id'',true),'''')::uuid or current_setting(''app.user_role'',true)=''admin'') with check (created_by=nullif(current_setting(''app.user_id'',true),'''')::uuid or current_setting(''app.user_role'',true)=''admin'')',t);
 end loop;
end $$;
create index if not exists ai_import_owner on ai_import_batches(tenant_id,created_by,created_at desc);
create index if not exists ai_feature_status on ai_feature_requests(tenant_id,status,created_at desc);
grant select,insert,update on ai_import_batches,ai_feature_requests,ai_work_conversations to dealos_app;
