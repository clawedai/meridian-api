from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class BulkImportRow(BaseModel):
    full_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    instagram_handle: Optional[str] = None
    company_domain: Optional[str] = None
    twitter_handle: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None


class BulkImportResult(BaseModel):
    total_rows: int
    created: int
    skipped: int
    failed: int
    errors: List[str] = []
    prospects: List[dict] = []  # first 5 created for preview


class BulkImportResponse(BaseModel):
    success: bool
    message: str
    created: int
    skipped: int
    failed: int
    errors: List[str] = []
    preview: List[dict] = []
