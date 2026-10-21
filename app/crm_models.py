from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class Address(Record):
    street_address: str | None = Field(default=None,max_length=1000)
    city: str | None = Field(default=None,max_length=200)
    state_region: str | None = Field(default=None,max_length=200)
    postal_code: str | None = Field(default=None,max_length=30)
    country: str | None = Field(default=None,max_length=100)

class Firm(Address):
    entity_kind: str | None = Field(default=None,max_length=100)
    observed_holding_sectors: str | None = Field(default=None,max_length=10000)
    firm_email: str | None = Field(default=None,max_length=320,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    firm_phone: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    domain: str | None = Field(default=None, max_length=253)
    website_url: str | None = Field(default=None, max_length=2048)
    linkedin_url: str | None = Field(default=None, max_length=2048)
    firm_type: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    headquarters: str | None = Field(default=None, max_length=300)
    sector_focus: str | None = Field(default=None, max_length=1000)
    geography: str | None = Field(default=None, max_length=1000)
    relationship_owner: str | None = Field(default=None, max_length=300)
    relationship_status: str = Field(default='unknown', max_length=100)
    notes: str | None = Field(default=None, max_length=20000)

    @field_validator('website_url', 'linkedin_url')
    @classmethod
    def public_link(cls, v, info):
        if not v:
            return None
        u = urlsplit(v)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            raise ValueError('Use an http or https website URL')
        if info.field_name == 'linkedin_url' and not (u.hostname == 'linkedin.com' or u.hostname.endswith('.linkedin.com')):
            raise ValueError('Use a LinkedIn URL')
        return v


class Criteria(Record):
    specialty_tags: list[str] = Field(default_factory=list,max_length=100)
    franchise_models: list[str] = Field(default_factory=list,max_length=30)
    sectors: list[str] = Field(default_factory=list, max_length=100)
    geographies: list[str] = Field(default_factory=list, max_length=100)
    financing_types: list[str] = Field(default_factory=list, max_length=30)
    stages: list[str] = Field(default_factory=list, max_length=30)
    transaction_types: list[str] = Field(default_factory=list, max_length=30)
    ownership_preferences: list[str] = Field(default_factory=list, max_length=30)
    excluded_sectors: list[str] = Field(default_factory=list, max_length=100)
    excluded_geographies: list[str] = Field(default_factory=list, max_length=100)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    check_min: Decimal | None = Field(default=None, ge=0)
    check_max: Decimal | None = Field(default=None, ge=0)
    revenue_min: Decimal | None = Field(default=None, ge=0)
    revenue_max: Decimal | None = Field(default=None, ge=0)
    ebitda_min: Decimal | None = None
    ebitda_max: Decimal | None = None

    @model_validator(mode='after')
    def ordered(self):
        for key in ('check', 'revenue', 'ebitda'):
            low, high = getattr(self, key+'_min'), getattr(self, key+'_max')
            if low is not None and high is not None and low > high:
                raise ValueError('Minimum must not exceed maximum')
        return self


class FundingProfile(Record):
    sector: str | None = Field(default=None, max_length=200)
    geography: str | None = Field(default=None, max_length=200)
    financing_type: str | None = Field(default=None, max_length=100)
    stage: str | None = Field(default=None, max_length=100)
    transaction_type: str | None = Field(default=None, max_length=100)
    ownership_preference: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    funding_amount: Decimal | None = Field(default=None, ge=0)
    ticket_min: Decimal | None = Field(default=None, ge=0)
    ticket_max: Decimal | None = Field(default=None, ge=0)
    revenue: Decimal | None = Field(default=None, ge=0)
    ebitda: Decimal | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.ticket_min is not None and self.ticket_max is not None and self.ticket_min > self.ticket_max:
            raise ValueError('Ticket minimum must not exceed maximum')
        return self


class Mandate(Record):
    name: str = Field(default='Investment criteria', min_length=1, max_length=300)
    criteria: Criteria
    evidence_status: Literal['banker_verified', 'source_stated', 'inferred', 'unverified'] = 'unverified'
    sources: list[str] = Field(default_factory=list, max_length=30)
    active: bool = True

    @model_validator(mode='after')
    def evidence_required(self):
        if self.evidence_status == 'source_stated' and not self.sources:
            raise ValueError('Source-stated criteria require source URLs')
        for source in self.sources:
            u=urlsplit(source)
            if u.scheme not in ('http','https') or not u.hostname or u.username:
                raise ValueError('Source URL must use http or https')
        return self
