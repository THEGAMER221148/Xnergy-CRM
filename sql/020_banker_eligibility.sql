alter table users add column if not exists relationship_banker boolean not null default true;
update users set relationship_banker=false where lower(email)='weststefany@icloud.com' and tenant_id in(select tenant_id from users where lower(email)='nia@xnergy.com');
