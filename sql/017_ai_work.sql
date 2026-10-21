alter table crm_tasks add column if not exists task_kind text not null default 'manual';
update crm_tasks t set task_kind='research' from investor_organizations o where t.organization_id=o.id and t.tenant_id=o.tenant_id and t.task_kind='manual' and t.title in ('Find contact details: '||o.name,'Complete investor profile: '||o.name,'Qualify SEC investor and find business contact: '||o.name);
create index if not exists ix_manual_tasks_assignee on crm_tasks(tenant_id,assigned_to,status) where task_kind='manual';
alter table investor_research_jobs add column if not exists paused boolean not null default false;
alter table investor_research_jobs add column if not exists priority integer not null default 0;
alter table investor_research_jobs add column if not exists saved_changes jsonb;
create index if not exists ix_research_ready on investor_research_jobs(tenant_id,priority desc,created_at,id) where status='pending' and not paused;
