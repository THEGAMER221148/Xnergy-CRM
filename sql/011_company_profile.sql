alter table company_prospects add column if not exists company_profile jsonb not null default '{}'::jsonb;
