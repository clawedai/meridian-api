"""
Pydantic schemas for Instagram organic intelligence signals.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class InstagramSignalsBase(BaseModel):
    instagram_handle: str
    is_active: bool = False
    followers: int = 0
    following: int = 0
    posts: int = 0
    instagram_intensity: int = Field(default=0, ge=0, le=25)
    instagram_active_score: int = Field(default=0, ge=0, le=15)
    engagement_rate: int = Field(default=0, ge=0, le=10)
    posting_frequency: int = Field(default=0, ge=0, le=10)
    follower_growth: int = Field(default=0, ge=0, le=5)
    hashtag_themes: List[str] = Field(default_factory=list)
    posts_analyzed: int = 0


class InstagramSignalsResponse(InstagramSignalsBase):
    id: Optional[str] = None
    user_id: Optional[str] = None
    prospect_id: Optional[str] = None
    fetched_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None
    url: Optional[str] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True


class InstagramRefreshRequest(BaseModel):
    prospect_id: str
    instagram_handle: str
