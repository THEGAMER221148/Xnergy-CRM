alter table crm_relationship_teams drop constraint if exists crm_relationship_teams_entity_kind_check;
alter table crm_relationship_teams add constraint crm_relationship_teams_entity_kind_check check(entity_kind in ('prospect','investor','deal','contact'));
alter table prospect_contacts add column if not exists notes text;
create unique index if not exists ux_prospect_contact_tenant_id on prospect_contacts(tenant_id,id);
alter table documents add column if not exists submitted_by_contact_id uuid;
alter table documents add constraint document_submitter_tenant_fk foreign key(tenant_id,submitted_by_contact_id) references prospect_contacts(tenant_id,id);
