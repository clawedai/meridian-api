"""
Google Ads API schemas — Pydantic models for Google Ads Transparency Report signals.

Table: google_ads_signals
Columns: user_id, prospect_id, company_domain, company_name,
         is_advertiser, ad_count, advertiser_name,
         google_ad_intensity, google_ad_keyword_themes, google_ad_recency,
         domains_advertised_on, last_active_date,
         fetched_at, first_seen_at, last_seen_at, updated_at, raw_response (JSONB)

Table: google_ads (individual ad records)
Columns: google_ad_signals_id, ad_id, ad_title, ad_description,
         ad_final_url, ad_display_url, ad_campaign,
         ad_first_seen, ad_last_seen
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


# =============================================
# GOOGLE AD (individual ad record)
# =============================================
class GoogleAdBase(BaseModel):
    ad_id: Optional[str] = None
    ad_title: Optional[str] = None
    ad_description: Optional[str] = None
    ad_final_url: Optional[str] = None
    ad_display_url: Optional[str] = None
    ad_campaign: Optional[str] = None
    ad_first_seen: Optional[datetime] = None
    ad_last_seen: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================
# GOOGLE ADS SIGNALS (aggregated per company)
# =============================================
class GoogleAdsSignalsBase(BaseModel):
    company_domain: str
    company_name: str
    is_advertiser: bool = False
    ad_count: int = 0
    advertiser_name: str = ""


class GoogleAdsSignalsResponse(GoogleAdsSignalsBase):
    id: str
    user_id: str
    prospect_id: Optional[str] = None
    google_ad_intensity: int = Field(default=0, ge=0, le=15)
    google_ad_keyword_themes: int = Field(default=0, ge=0, le=15)
    google_ad_recency: int = Field(default=0, ge=0, le=15)
    google_ad_active: bool = False
    domains_advertised_on: List[str] = Field(default_factory=list)
    last_active_date: Optional[datetime] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    ads: List[GoogleAdBase] = Field(default_factory=list)
    keyword_themes_data: Optional[dict] = None

    class Config:
        from_attributes = True


# =============================================
# REQUEST / UPDATE MODELS
# =============================================
class GoogleAdsSearchRequest(BaseModel):
    company_name: str
    company_domain: Optional[str] = None


class GoogleAdsRefreshRequest(BaseModel):
    company_domain: str
    company_name: str


class GoogleAdsSignalsUpdate(BaseModel):
    """Used internally for updating intent_scores from google_ads signals."""
    google_ad_active: bool = False
    google_ad_intensity: int = 0
    google_ad_keyword_themes: int = 0
    google_ad_recency: int = 0
