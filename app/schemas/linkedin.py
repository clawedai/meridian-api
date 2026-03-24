"""
LinkedIn API schemas.
"""
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class LinkedInLoginRequest(BaseModel):
    email: EmailStr
    password: str


class LinkedInLoginResponse(BaseModel):
    success: bool
    username: Optional[str] = None
    error: Optional[str] = None


class LinkedInScrapeRequest(BaseModel):
    prospect_id: str
    url: str  # LinkedIn profile or company URL


class LinkedInScrapeResponse(BaseModel):
    success: bool
    prospect_id: str
    posts_found: int = 0
    hiring_active: bool = False
    open_roles: int = 0
    score_delta: int = 0
    error: Optional[str] = None


class LinkedInStatusResponse(BaseModel):
    logged_in: bool
    username: Optional[str] = None
    last_used_at: Optional[datetime] = None
    is_valid: bool = True
