from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    company_industry: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    company_industry: Optional[str] = None
    avatar_url: Optional[str] = None
    weekly_report_preference: Optional[str] = None
    email_notifications_enabled: Optional[bool] = None

class UserResponse(UserBase):
    id: str
    avatar_url: Optional[str] = None
    subscription_tier: str = "starter"
    subscription_status: str = "trialing"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class TokenPayload(BaseModel):
    sub: str
    exp: int
