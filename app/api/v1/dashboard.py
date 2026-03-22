from fastapi import APIRouter, Depends, Query
from typing import List
import httpx
import logging
from datetime import datetime, timedelta
from ..deps import get_current_user, get_supabase, SupabaseClient
from ...schemas.entity import DashboardStats, InsightResponse, MomentumStats, CompetitiveStats
from ...services.momentum import MomentumEngine
from ...services.benchmark import BenchmarkEngine

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = logging.getLogger(__name__)


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get dashboard statistics for current user"""
    user_id = current_user["id"]
    headers = supabase._get_headers()

    entity_count = 0
    active_source_count = 0
    unread_insight_count = 0
    active_alert_count = 0
    insights_this_week = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Entity count
            r = await client.get(
                f"{supabase.url}/rest/v1/entities?user_id=eq.{user_id}&is_archived=eq.false&select=id",
                headers=headers
            )
            if r.status_code == 200:
                entity_count = len(r.json())

            # Active sources
            r = await client.get(
                f"{supabase.url}/rest/v1/sources?user_id=eq.{user_id}&is_active=eq.true&select=id",
                headers=headers
            )
            if r.status_code == 200:
                active_source_count = len(r.json())

            # Unread insights
            r = await client.get(
                f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&is_read=eq.false&is_archived=eq.false&select=id",
                headers=headers
            )
            if r.status_code == 200:
                unread_insight_count = len(r.json())

            # Active alerts
            r = await client.get(
                f"{supabase.url}/rest/v1/alerts?user_id=eq.{user_id}&is_active=eq.true&select=id",
                headers=headers
            )
            if r.status_code == 200:
                active_alert_count = len(r.json())

            # Insights this week - use explicit timestamp, not now()
            week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
            r = await client.get(
                f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&is_archived=eq.false&generated_at=gte.{week_ago}&select=id",
                headers=headers
            )
            if r.status_code == 200:
                insights_this_week = len(r.json())
    except Exception as e:
        logger.error(f"Dashboard stats error: {e}")

    return DashboardStats(
        entity_count=entity_count,
        active_source_count=active_source_count,
        unread_insight_count=unread_insight_count,
        active_alert_count=active_alert_count,
        insights_this_week=insights_this_week,
        last_insight_at=None,
    )


@router.get("/recent-insights", response_model=List[InsightResponse])
async def get_recent_insights(
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get recent insights for dashboard"""
    user_id = current_user["id"]
    headers = supabase._get_headers()

    try:
        url = f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&is_archived=eq.false&order=generated_at.desc&limit={limit}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                insights = response.json()
                return [InsightResponse(**i) for i in insights]
    except Exception as e:
        logger.error(f"Recent insights error: {e}")


@router.get("/momentum", response_model=MomentumStats)
async def get_signal_momentum(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Get signal velocity and momentum metrics for all tracked entities.

    Shows:
    - Top movers: entities with the highest momentum scores (accelerating)
    - Top decelerators: entities slowing down
    - Most active: highest raw signal volume
    - Industry heat index: total activity across all tracked competitors
    - Industry heat change: week-over-week change in overall activity

    Momentum score: -100 to +100
    - Positive = accelerating (more signals this week vs last)
    - Negative = decelerating
    - 0 = stable
    """
    user_id = current_user["id"]
    engine = MomentumEngine(user_id=user_id, supabase=supabase)
    try:
        return await engine.compute_momentum_stats()
    except Exception as e:
        logger.error(f"Momentum stats error: {e}")
        return MomentumStats()


@router.get("/competitive", response_model=CompetitiveStats)
async def get_competitive_benchmarking(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Get competitive benchmarking data — how tracked entities compare to each other.

    Shows:
    - Industry benchmarks: auto-grouped by industry field (requires 2+ entities in same industry)
    - Manual groups: user-created competitive groups
    - Top entities: ranked by share of attention across all groups

    Each entity shows:
    - share_of_attention: % of total group signals
    - competitive_delta: WoW change in share (positive = gaining share)
    - rank_in_group: 1 = dominant player

    Requires at least 2 entities in a group for meaningful comparison.
    """
    user_id = current_user["id"]
    engine = BenchmarkEngine(user_id=user_id, supabase=supabase)
    try:
        return await engine.compute_competitive_stats()
    except Exception as e:
        logger.error(f"Competitive benchmarking error: {e}")
        return CompetitiveStats()
