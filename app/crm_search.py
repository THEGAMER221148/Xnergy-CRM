"""Parameter-bound investor search. Criteria filters apply to one active mandate."""
from decimal import Decimal

def investor_query(q='',sector='',geography='',category='',financing='',stage='',minimum=None,maximum=None,currency='USD',limit=50,offset=0):
    if minimum is not None and maximum is not None and minimum>maximum:
        raise ValueError('Minimum investment must not exceed maximum')
    args=[]
    def bind(value):
        args.append(value)
        return '$'+str(len(args))
    conditions=[]
    if q:
        p=bind(q)
        conditions.append(f"(o.name ilike '%'||{p}||'%' or coalesce(o.domain,'') ilike '%'||{p}||'%' or exists(select 1 from investor_contacts ct where ct.organization_id=o.id and concat_ws(' ',ct.name,ct.email,ct.phone) ilike '%'||{p}||'%'))")
    for column,value in [('sector_focus',sector),('geography',geography),('firm_type',category)]:
        if value:conditions.append(f"coalesce(o.{column},'') ilike '%'||{bind(value)}||'%'")
    mandates=[]
    for key,value in [('financing_types',financing),('stages',stage)]:
        if value:
            p=bind(value)
            mandates.append(f"exists(select 1 from jsonb_array_elements_text(coalesce(m.criteria->'{key}','[]'::jsonb)) v where v ilike '%'||{p}||'%')")
    if minimum is not None or maximum is not None:
        mandates.append(f"m.criteria->>'currency'={bind(currency)}")
        mandates.append("(nullif(m.criteria->>'check_min','') is not null or nullif(m.criteria->>'check_max','') is not null)")
        if minimum is not None:mandates.append(f"(nullif(m.criteria->>'check_max','') is null or (m.criteria->>'check_max')::numeric >= {bind(minimum)}::numeric)")
        if maximum is not None:mandates.append(f"(nullif(m.criteria->>'check_min','') is null or (m.criteria->>'check_min')::numeric <= {bind(maximum)}::numeric)")
    if mandates:conditions.append('exists(select 1 from investor_mandates m where m.organization_id=o.id and m.active and '+' and '.join(mandates)+')')
    where=' and '.join(conditions) or 'true'
    sql=f'''select o.id,o.name,o.domain,o.website_url,o.firm_type,o.sector_focus,o.geography,o.relationship_owner,
       (select count(*) from investor_contacts ct where ct.organization_id=o.id) contact_count,
       (select max(ct.last_contact_at) from investor_contacts ct where ct.organization_id=o.id) last_contact
       from investor_organizations o where {where} order by o.name,o.id limit {bind(limit)} offset {bind(offset)}'''
    return sql,args
