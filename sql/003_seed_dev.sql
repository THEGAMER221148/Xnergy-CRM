-- Development only. Never execute in production.
insert into tenants(name,slug) values('Xnergy Financial','xnergy') on conflict(slug) do nothing;
insert into users(tenant_id,email,display_name,role)
select id,'admin@xnergy.local','Xnergy Development Admin','admin' from tenants where slug='xnergy'
on conflict(tenant_id,email) do nothing;
