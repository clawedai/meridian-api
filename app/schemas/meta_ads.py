"""
Meta Ads API schemas — Pydantic models for Meta/Facebook Ads Library signals.
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# =============================================
# META AD (individual ad record)
# =============================================
class MetaAdBase(BaseModel):
    ad_id: str
    page_id: Optional[str] = None
    ad_creative_body: Optional[str] = None
    ad_creative_link: Optional[str] = None
    ad_snapshot_url: Optional[str] = None
    ad_status: str = "ACTIVE"
    ad_delivery_start: Optional[datetime] = None
    ad_delivery_end: Optional[datetime] = None
    is_lead_gen: bool = False
    is_brand_awareness: bool = False
    is_conversion: bool = False


# =============================================
# META AD SIGNALS (aggregated per company)
# =============================================
class MetaAdSignalsBase(BaseModel):
    company_domain: str
    company_name: str
    fb_page_id: Optional[str] = None
    fb_page_url: Optional[str] = None
    is_advertiser: bool = False
    ad_count: int = 0
    meta_ad_intensity: int = Field(default=0, ge=0, le=25)
    meta_ad_lead_gen: bool = False
    meta_ad_recency: int = Field(default=0, ge=0, le=15)
    meta_ad_active: bool = False
    fetched_at: Optional[datetime] = None


class MetaAdSignalsResponse(MetaAdSignalsBase):
    id: str
    user_id: str
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    ads: list["MetaAdBase"] = []

    class Config:
        from_attributes = True


# =============================================
# REQUEST / UPDATE MODELS
# =============================================
class MetaAdSearchRequest(BaseModel):
    company_name: str
    company_domain: Optional[str] = None


class MetaAdRefreshRequest(BaseModel):
    company_domain: str
    company_name: str


class MetaAdSignalsUpdate(BaseModel):
    """Used internally for updating intent_scores from meta_ad signals."""
    meta_ad_active: bool = False
    meta_ad_lead_gen: bool = False
    meta_ad_intensity: int = 0
    meta_ad_recency: int = 0
