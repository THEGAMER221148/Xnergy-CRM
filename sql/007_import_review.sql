alter table crm_source_records add column prospect_id uuid;
alter table crm_source_records add constraint crm_source_prospect_tenant_fk foreign key(tenant_id,prospect_id) references company_prospects(tenant_id,id);
create index ix_crm_source_status on crm_source_records(tenant_id,status);
