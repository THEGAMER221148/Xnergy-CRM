"""Xnergy qualification rules approved by Nia, September 22 2026."""
def prospect_fit(row):
 p=row.get('company_profile') or {}
 revenue=p.get('annual_revenue');currency=p.get('currency')
 if revenue is None:status='Needs revenue research'
 elif currency!='USD':status='Needs USD revenue confirmation'
 elif revenue>=5000000:status='Meets revenue minimum'
 else:status='Below $5M revenue minimum'
 country=(row.get('country') or '').strip().lower()
 return {'revenue_status':status,'us_preferred':country in ('us','usa','united states','united states of america'),'policy':'$5M minimum annual revenue; US preferred; any sector; capital sought unrestricted'}
