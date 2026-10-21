alter table investor_organizations add column if not exists entity_kind text;
alter table investor_organizations add column if not exists observed_holding_sectors text;
alter table investor_organizations add column if not exists sec_cik text;
create unique index if not exists ux_investor_sec_cik on investor_organizations(tenant_id,sec_cik) where sec_cik is not null;
create index if not exists ix_sec_filing_issuer on crm_source_records(tenant_id,(raw_record->>'issuer_cik')) where source_key like 'sec:filing:%';
