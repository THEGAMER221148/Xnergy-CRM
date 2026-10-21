create table if not exists library_current_documents (
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references tenants(id),
 purpose text not null,
 drive_id text not null,
 item_id text not null,
 marked_by uuid not null references users(id),
 marked_at timestamptz not null default now(),
 unique(tenant_id,purpose)
);
alter table library_current_documents enable row level security;
alter table library_current_documents force row level security;
drop policy if exists library_current_tenant on library_current_documents;
create policy library_current_tenant on library_current_documents using(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','banker','deal_manager','compliance')) with check(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid and current_setting('app.user_role',true) in ('admin','deal_manager','compliance'));
grant select,insert,update,delete on library_current_documents to dealos_app;
