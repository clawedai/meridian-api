from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime


# =============================================
# PROSPECTS
# =============================================
class ProspectBase(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    company_domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    instagram_handle: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    source: str = "manual"
    prospect_type: str = "prospect"  # prospect | client | competitor
    notes: Optional[str] = None


class ProspectCreate(ProspectBase):
    pass


class ProspectUpdate(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    company_domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    instagram_handle: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    suppressed: Optional[bool] = None


class ProspectResponse(ProspectBase):
    id: str
    user_id: str
    raw_data: dict = {}
    confidence_score: float = 0.0
    last_enriched_at: Optional[datetime] = None
    suppressed: bool = False
    created_at: datetime
    intent_score: Optional[dict] = None  # Joined intent_score data

    class Config:
        from_attributes = True


# =============================================
# LINKEDIN POSTS (Module 01)
# =============================================
class LinkedInPostResponse(BaseModel):
    id: str
    prospect_id: str
    post_text: Optional[str] = None
    post_url: Optional[str] = None
    posted_at: Optional[datetime] = None
    engagement_likes: int = 0
    engagement_comments: int = 0
    engagement_shares: int = 0
    scraped_at: datetime

    class Config:
        from_attributes = True


class LinkedInScrapeRequest(BaseModel):
    prospect_id: str
    linkedin_url: Optional[str] = None
    company_domain: Optional[str] = None


# =============================================
# PAIN POINTS (Module 01)
# =============================================
class PainPointResponse(BaseModel):
    id: str
    prospect_id: str
    source_post_id: Optional[str] = None
    pain_category: Optional[str] = None  # tool_frustration, process_pain, growth_blocker
    pain_description: Optional[str] = None
    tools_mentioned: List[str] = []
    goals_expressed: List[str] = []
    sentiment: str = "neutral"  # positive, neutral, negative, frustrated
    confidence_score: float = 0.0
    extracted_at: datetime

    class Config:
        from_attributes = True


# =============================================
# FUNDING SIGNALS (Module 04)
# =============================================
class FundingSignalResponse(BaseModel):
    id: str
    prospect_id: str
    company_name: Optional[str] = None
    funding_amount: Optional[str] = None
    funding_stage: Optional[str] = None  # seed, series_a, series_b, etc.
    announced_date: Optional[datetime] = None
    source_url: Optional[str] = None
    intent_score_boost: int = 40
    scraped_at: datetime

    class Config:
        from_attributes = True


class FundingSignalCreate(BaseModel):
    prospect_id: str
    company_name: str
    funding_amount: Optional[str] = None
    funding_stage: Optional[str] = None
    announced_date: Optional[datetime] = None
    source_url: Optional[str] = None


# =============================================
# INTENT SCORES (Module 05)
# =============================================
class IntentScoreResponse(BaseModel):
    id: str
    prospect_id: str
    score: float = 0.0
    tier: str = "cold"  # hot, warm, cold
    funding_signal: bool = False
    hiring_signal: bool = False
    review_signal: bool = False
    linkedin_signal: bool = False
    technographic_signal: bool = False
    website_visit_signal: bool = False
    meta_ad_signal: bool = False
    reddit_signal: bool = False
    instagram_signal: bool = False
    last_updated_at: datetime
    score_breakdown: dict = {}

    class Config:
        from_attributes = True


class IntentScoreUpdate(BaseModel):
    score: Optional[float] = None
    tier: Optional[str] = None
    funding_signal: Optional[bool] = None
    hiring_signal: Optional[bool] = None
    review_signal: Optional[bool] = None
    linkedin_signal: Optional[bool] = None
    technographic_signal: Optional[bool] = None
    website_visit_signal: Optional[bool] = None
    meta_ad_signal: Optional[bool] = None
    reddit_signal: Optional[bool] = None
    instagram_signal: Optional[bool] = None
    score_breakdown: Optional[dict] = None


# =============================================
# TECHNOGRAPHICS (Module 06)
# =============================================
class TechnographicResponse(BaseModel):
    id: str
    prospect_id: str
    company_domain: Optional[str] = None
    tool_name: Optional[str] = None
    tool_category: Optional[str] = None  # CRM, EMAIL, ANALYTICS, SALES_INTEL, MARKETING
    is_competitor_tool: bool = False
    enriched_at: datetime

    class Config:
        from_attributes = True


class TechnographicEnrichRequest(BaseModel):
    prospect_id: str
    company_domain: Optional[str] = None


# =============================================
# REVIEW SIGNALS (Module 12)
# =============================================
class ReviewSignalResponse(BaseModel):
    id: str
    prospect_id: str
    competitor_name: Optional[str] = None
    review_platform: Optional[str] = None  # G2, Capterra, Trustpilot
    reviewer_role: Optional[str] = None
    rating: Optional[int] = None
    review_text: Optional[str] = None
    switching_intent: bool = False
    pain_mentioned: Optional[str] = None
    scraped_at: datetime

    class Config:
        from_attributes = True


class ReviewScrapeRequest(BaseModel):
    competitor_names: List[str]


# =============================================
# DRAFT EMAILS (Module 17)
# =============================================
class DraftEmailResponse(BaseModel):
    id: str
    prospect_id: str
    trigger_signal_type: Optional[str] = None  # funding, hiring, review, linkedin_post
    trigger_signal_id: Optional[str] = None
    subject_line: Optional[str] = None
    first_line: Optional[str] = None
    full_email_body: Optional[str] = None
    signal_context: Optional[str] = None
    approved: bool = False
    sent: bool = False
    generated_at: datetime

    class Config:
        from_attributes = True


class DraftEmailApprove(BaseModel):
    approved: bool = True


# =============================================
# SCORING CONFIG
# =============================================
# Score weights based on playbook
SCORE_WEIGHTS = {
    "funding": 40,           # Series A/B = big budget
    "hiring": 20,            # Hiring revops/sales = actively evaluating
    "review_negative": 15,    # 1-3 star reviews on competitor
    "review_switching": 25,   # Explicit switching intent
    "linkedin_pain": 10,     # Posts about frustrations
    "linkedin_frustrated": 15,  # Frustrated sentiment
    "technographic": 5,       # Using CRM but no sales intel tool
    "website_visit": 30,      # Visited pricing page
    # Meta Ad signals (Module 05 extension)
    "meta_ad_active": 25,      # Active ads in last 30 days
    "meta_ad_intensity": 15,   # 3+ ads OR >21 days running
    "meta_ad_lead_gen": 10,    # Lead gen ad format
    "meta_ad_recency": 5,      # First seen within 7 days
    # Reddit signals (Module 05 extension)
    "reddit_ad_active": 20,        # Company advertising on Reddit
    "reddit_organic_active": 10,  # Active Reddit mentions (5+ posts)
    "reddit_positive_sentiment": 10,  # Positive Reddit community sentiment
    "reddit_intensity": 5,         # 10+ mentions OR 50+ upvotes
    # Google Ads signals (Module 05 extension)
    "google_ad_active": 20,          # Company advertising on Google
    "google_ad_intensity": 15,       # 3+ campaigns OR high ad volume
    "google_ad_keyword_themes": 10,  # 3+ high-intent B2B keywords found
    # Instagram organic signals (Module 18)
    "instagram_active": 15,          # Active Instagram presence
    "instagram_engagement": 10,      # Avg engagement > 3%
    "instagram_posting_frequency": 10,  # Posts at least 3x per week
    "instagram_follower_growth": 5,   # Follower count > 1000
}

# Tier thresholds
TIER_HOT = 50.0
TIER_WARM = 20.0
TIER_COLD = 0.0

# Competitor tools list
COMPETITOR_TOOLS = [
    "apollo", "zoominfo", "6sense", "bombora", "clearbit",
    "hunter", "snov", "memos", "phanton", "凡尘"
]
