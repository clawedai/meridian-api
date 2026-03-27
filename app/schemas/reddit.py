"""
Reddit API schemas — Pydantic models for Reddit ad and organic signals.
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# =============================================
# REDDIT AD SIGNALS
# =============================================
class RedditAdSignalsBase(BaseModel):
    company_domain: str
    company_name: str
    is_advertiser: bool = False
    ad_count: int = 0
    promoted_posts_found: int = 0


class RedditAdSignalsResponse(RedditAdSignalsBase):
    id: str
    user_id: str
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================
# REDDIT ORGANIC SIGNALS
# =============================================
class RedditOrganicSignalsBase(BaseModel):
    company_domain: str
    company_name: str
    mention_count: int = 0
    sentiment_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    sentiment_label: str = "neutral"
    positive_mentions: int = 0
    negative_mentions: int = 0
    subreddit_count: int = 0
    total_upvotes: int = 0
    total_comments: int = 0
    reddit_intensity: int = Field(default=0, ge=0, le=20)
    reddit_organic_active: bool = False


class RedditOrganicSignalsResponse(RedditOrganicSignalsBase):
    id: str
    user_id: str
    last_post_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================
# REQUEST / UPDATE MODELS
# =============================================
class RedditSearchRequest(BaseModel):
    company_name: str
    company_domain: Optional[str] = None


class RedditRefreshRequest(BaseModel):
    company_domain: str
    company_name: str


class RedditAdSignalsUpdate(BaseModel):
    """Used internally for updating intent_scores from reddit_ad signals."""
    is_advertiser: bool = False
    ad_count: int = 0
    promoted_posts_found: int = 0


class RedditOrganicSignalsUpdate(BaseModel):
    """Used internally for updating intent_scores from reddit_organic signals."""
    reddit_organic_active: bool = False
    reddit_intensity: int = 0
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
