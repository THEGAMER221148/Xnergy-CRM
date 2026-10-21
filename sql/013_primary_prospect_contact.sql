alter table prospect_contacts add column if not exists is_primary boolean not null default false;
create unique index if not exists ux_primary_prospect_contact on prospect_contacts(tenant_id,prospect_id) where is_primary;
