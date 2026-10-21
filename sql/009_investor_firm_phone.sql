-- Apply before deploying the firm-phone input change. Existing records remain unchanged.
alter table investor_organizations add column if not exists firm_phone text;
