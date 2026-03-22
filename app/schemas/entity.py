from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime
from enum import Enum

class SourceType(str, Enum):
    RSS = "rss"
    SCRAPE = "scrape"
    API = "api"
    MANUAL = "manual"

class SourceStatus(str, Enum):
    ACTIVE = "active"
    WARNING = "warning"
    ERROR = "error"
    INACTIVE = "inactive"

class InsightImportance(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class InsightType(str, Enum):
    SUMMARY = "summary"
    ANOMALY = "anomaly"
    COMPARISON = "comparison"
    TREND = "trend"
    ALERT = "alert"
    FUNDING = "funding"
    PRODUCT = "product"
    HIRING = "hiring"
    PR = "pr"
    LEADERSHIP = "leadership"
    PARTNERSHIP = "partnership"
    PREDICTION = "prediction"

# Entity Schemas
class EntityBase(BaseModel):
    name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = []

class EntityCreate(EntityBase):
    pass

class EntityUpdate(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    is_archived: Optional[bool] = None

class EntityResponse(EntityBase):
    id: str
    user_id: str
    logo_url: Optional[str] = None
    is_archived: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Source Schemas
class SourceBase(BaseModel):
    name: str
    source_type: SourceType
    url: Optional[str] = None
    config: dict = {}
    refresh_interval_minutes: int = 360

class SourceCreate(SourceBase):
    entity_id: str

class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    config: Optional[dict] = None
    refresh_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None

class SourceResponse(SourceBase):
    id: str
    user_id: str
    entity_id: str
    status: SourceStatus = SourceStatus.ACTIVE
    last_fetched_at: Optional[datetime] = None
    last_error: Optional[str] = None
    fetch_count: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Insight Schemas
class InsightBase(BaseModel):
    title: str
    content: str
    insight_type: InsightType
    importance: InsightImportance = InsightImportance.MEDIUM
    summary: Optional[str] = None

class InsightCreate(InsightBase):
    entity_id: str
    source_ids: List[str] = []

class InsightUpdate(BaseModel):
    is_read: Optional[bool] = None
    is_archived: Optional[bool] = None

class InsightResponse(InsightBase):
    id: str
    user_id: str
    entity_id: str
    confidence: float
    source_ids: List[str] = []
    is_read: bool = False
    is_archived: bool = False
    generated_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True

# Alert Schemas
class AlertConditionType(str, Enum):
    KEYWORD = "keyword"
    CHANGE = "change"
    METRIC = "metric"
    PATTERN = "pattern"
    SCHEDULE = "schedule"

class AlertChannel(str, Enum):
    EMAIL = "email"
    WEBHOOK = "webhook"
    DASHBOARD = "dashboard"

class AlertBase(BaseModel):
    name: str
    alert_condition_type: AlertConditionType
    condition_config: dict = {}
    channels: List[AlertChannel] = [AlertChannel.EMAIL]
    webhook_url: Optional[str] = None
    email_frequency: str = "immediate"

class AlertCreate(AlertBase):
    entity_id: Optional[str] = None
    description: Optional[str] = None

class AlertUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    condition_config: Optional[dict] = None
    channels: Optional[List[AlertChannel]] = None
    webhook_url: Optional[str] = None
    email_frequency: Optional[str] = None
    is_active: Optional[bool] = None

class AlertResponse(AlertBase):
    id: str
    user_id: str
    entity_id: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Dashboard Stats
class DashboardStats(BaseModel):
    entity_count: int = 0
    active_source_count: int = 0
    unread_insight_count: int = 0
    active_alert_count: int = 0
    insights_this_week: int = 0
    last_insight_at: Optional[datetime] = None


# Momentum / Signal Velocity
class MomentumDirection(str, Enum):
    ACCELERATING = "accelerating"  # momentum score > 10
    STABLE = "stable"              # -10 <= momentum score <= 10
    DECELERATING = "decelerating"  # momentum score < -10


class MomentumEntity(BaseModel):
    entity_id: str
    entity_name: str
    industry: Optional[str] = None
    insights_this_week: int = 0
    insights_last_week: int = 0
    velocity_percent: float = 0.0       # week-over-week % change
    momentum_score: float = 0.0          # -100 to +100
    direction: MomentumDirection = MomentumDirection.STABLE
    top_insight_types: List[str] = []
    avg_importance: str = "low"


class MomentumStats(BaseModel):
    top_movers: List[MomentumEntity] = []      # highest momentum scores
    top_decelerators: List[MomentumEntity] = []   # lowest momentum scores
    most_active: List[MomentumEntity] = []       # highest raw volume
    industry_heat_index: float = 0.0             # total signals across all tracked entities
    industry_heat_change: float = 0.0             # WoW change in industry activity
    total_entities_tracked: int = 0


# Predictive Engine (Pillar 4)
class PredictionInsight(BaseModel):
    """A prediction insight generated from historical pattern analysis."""
    entity_id: str
    entity_name: str
    antecedent_event: str          # what happened (e.g., "funding_announced")
    consequent_event: str          # what we predict will happen (e.g., "hiring_spike")
    confidence: float = 0.0        # 0.0 to 1.0
    predicted_window_start_days: int = 0  # earliest expected
    predicted_window_end_days: int = 0    # latest expected
    pattern_lag_days: int = 0       # typical lag in days
    observed_count: int = 0         # how many times we've seen this pattern
    generated_at: str               # when this prediction was generated
    rationale: str = ""             # natural language explanation
    importance: InsightImportance = InsightImportance.MEDIUM
    insight_type: str = "prediction"


class PredictionStats(BaseModel):
    """Stats about predictions generated for a user."""
    total_predictions: int = 0
    high_confidence_predictions: int = 0  # confidence > 0.7
    predictions_by_entity: int = 0        # unique entities with predictions
    pattern_count: int = 0                # total patterns learned
    most_common_patterns: List[str] = []   # e.g. ["funding → hiring", "product launch → PR spike"]


# Competitive Benchmarking
class CompetitiveEntity(BaseModel):
    entity_id: str
    entity_name: str
    industry: Optional[str] = None
    insights_this_week: int = 0
    insights_last_week: int = 0
    share_of_attention: float = 0.0       # % of total industry signals this entity captures
    competitive_delta: float = 0.0         # WoW change in share (positive = gaining share)
    rank_in_group: int = 0               # 1 = dominant player
    group_name: str = "Industry"         # "Technology", "EV Market", etc.
    group_type: str = "industry"          # "industry" or "manual"


class CompetitiveGroupStats(BaseModel):
    group_name: str
    group_type: str  # "industry" or "manual"
    total_entities: int = 0
    total_signals_this_week: int = 0
    total_signals_last_week: int = 0
    industry_heat_index: float = 0.0     # total signals in this competitive set
    heat_change_percent: float = 0.0      # WoW % change
    top_player: Optional[str] = None     # entity with highest share
    fastest_rising: Optional[str] = None # entity with highest delta


class CompetitiveStats(BaseModel):
    industry_benchmarks: List[CompetitiveGroupStats] = []
    manual_groups: List[CompetitiveGroupStats] = []
    top_entities: List[CompetitiveEntity] = []   # across all groups, ranked by share
    total_industries_tracked: int = 0
    total_groups: int = 0


# Competitive Groups (manual user-created groups)
class CompetitiveGroupBase(BaseModel):
    name: str


class CompetitiveGroupCreate(CompetitiveGroupBase):
    entity_ids: List[str] = []


class CompetitiveGroupUpdate(BaseModel):
    name: Optional[str] = None
    entity_ids: Optional[List[str]] = None


class CompetitiveGroupResponse(CompetitiveGroupBase):
    id: str
    user_id: str
    entity_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# Report Schemas
class ReportType(str, Enum):
    WEEKLY_DIGEST = "weekly_digest"
    MONTHLY_SUMMARY = "monthly_summary"
    ENTITY_REPORT = "entity_report"
    COMPETITIVE_ANALYSIS = "competitive_analysis"
    CUSTOM = "custom"

class ReportStatus(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"

class ReportCreate(BaseModel):
    """Schema for creating a new report"""
    report_type: ReportType
    title: str
    entity_ids: List[str] = []
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


class Report(BaseModel):
    id: str
    user_id: str
    entity_ids: List[str] = []
    report_type: ReportType
    title: str
    content: dict = {}
    html_content: Optional[str] = None
    pdf_url: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: ReportStatus = ReportStatus.GENERATING
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True
