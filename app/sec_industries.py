"""Map issuer SIC to broad DealOS sectors; these describe holdings, not mandates."""
def holding_sector(sic):
    try:n=int(sic)
    except (ValueError,TypeError):return None
    if 2830<=n<=2839 or 3840<=n<=3859 or 8000<=n<=8099:return 'Healthcare'
    if 3570<=n<=3579 or 3660<=n<=3679 or 7370<=n<=7379:return 'Technology'
    if 6000<=n<=6499 or 6700<=n<=6799:return 'Financial Services'
    if 6500<=n<=6599 or 1500<=n<=1799:return 'Real Estate & Construction'
    if 1300<=n<=1399 or 2900<=n<=2999:return 'Energy'
    if 4900<=n<=4999:return 'Utilities & Infrastructure'
    if 4000<=n<=4799:return 'Transportation & Logistics'
    if 4800<=n<=4899 or 2700<=n<=2799 or 7800<=n<=7899:return 'Communications & Media'
    if 100<=n<=999 or 2000<=n<=2099 or 5800<=n<=5899:return 'Food, Beverage & Agriculture'
    if 8200<=n<=8299:return 'Education & Training'
    if 7000<=n<=7099 or 7900<=n<=7999:return 'Travel, Hospitality & Leisure'
    if 1000<=n<=1299 or 1400<=n<=1499 or 2600<=n<=2699 or 2800<=n<=2899:return 'Materials & Natural Resources'
    if 7300<=n<=7399 or 8700<=n<=8799:return 'Business Services'
    if 5200<=n<=5799 or 5900<=n<=5999 or 7200<=n<=7299:return 'Consumer Products & Services'
    if 3000<=n<=3999:return 'Industrials'
    return None

async def enrich_holding_sectors(client,user):
    import asyncio,json
    from .db import tenant_tx
    async with tenant_tx(user) as c:
        issuers=await c.fetch("""select distinct raw_record->>'issuer_cik' cik from crm_source_records f
          where source_key like 'sec:filing:%' and not exists(select 1 from crm_source_records i where i.source_key='sec:issuer:'||(f.raw_record->>'issuer_cik'))""")
    done=0
    for issuer in issuers:
        cik=issuer['cik']
        if not cik or not cik.isdigit():continue
        url='https://data.sec.gov/submissions/CIK'+cik.zfill(10)+'.json'
        try:
            response=await client.get(url)
            if response.status_code in (403,429):
                print('SEC industry lookup paused: HTTP '+str(response.status_code),flush=True);break
            response.raise_for_status();data=response.json()
            if str(int(data['cik']))!=str(int(cik)):continue
            payload={'source':'SEC','issuer_cik':cik,'issuer_name':data.get('name'),'sic':data.get('sic'),'sic_description':data.get('sicDescription'),'sector':holding_sector(data.get('sic')),'sector_basis':'Derived from disclosed issuer SIC; not an investor mandate','source_url':url}
            async with tenant_tx(user) as c:
                await c.execute("insert into crm_source_records(tenant_id,source_key,filename,source_url,raw_record,status) values($1::uuid,$2,$3,$4,$5::jsonb,'sec_issuer_metadata') on conflict(tenant_id,source_key) do nothing",user['tenant_id'],'sec:issuer:'+cik,'CIK'+cik.zfill(10)+'.json',url,json.dumps(payload))
                if payload['sector']:
                    await c.execute("""update investor_organizations o set observed_holding_sectors=(
                      select string_agg(distinct trim(v),'; ' order by trim(v)) from unnest(string_to_array(coalesce(o.observed_holding_sectors,'')||';'||$2,';')) v where trim(v)<>'')
                      where o.source='SEC' and o.id in(select organization_id from crm_source_records where source_key like 'sec:filing:%' and raw_record->>'issuer_cik'=$1)""",cik,payload['sector'])
            done+=1
        except Exception as error:print('SEC issuer lookup failed: '+cik+' '+type(error).__name__,flush=True)
        if done and done%100==0:print('SEC issuer industries checked: '+str(done),flush=True)
        await asyncio.sleep(.3)
    print('SEC issuer industries populated: '+str(done),flush=True)
