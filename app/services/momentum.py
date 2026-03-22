"""
Signal Velocity Engine — Pillar 1 of the 4-Pillar Intelligence Strategy.

Tracks how fast competitors are making moves, not just what they do.
Computes momentum scores, acceleration, and industry heat index.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import Counter

import httpx
from app.api.deps import get_supabase, SupabaseClient
from app.schemas.entity import (
    MomentumStats,
    MomentumEntity,
    MomentumDirection,
    InsightImportance,
)

logger = logging.getLogger(__name__)

# Importance weights for momentum scoring
IMPORTANCE_WEIGHTS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
    None: 1.0,
}

# Velocity thresholds
ACCELERATING_THRESHOLD = 10.0    # momentum_score > +10
DECELERATING_THRESHOLD = -10.0   # momentum_score < -10


class MomentumEngine:
    """Computes signal velocity and momentum scores for tracked entities."""

    def __init__(self, user_id: str, supabase: Optional[SupabaseClient] = None):
        self.user_id = user_id
        self.supabase = supabase or get_supabase()

    def _get_headers(self) -> dict:
        return self.supabase._get_headers()

    async def _fetch_insights_for_entity(
        self, entity_id: str, days: int = 7
    ) -> List[Dict]:
        """Fetch insights for an entity within the last N days."""
        from_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        url = self.supabase.build_url(
            "insights",
            [
                f"user_id=eq.{self.user_id}",
                f"entity_id=eq.{entity_id}",
                f"is_archived=eq.false",
                f"generated_at=gte.{from_date}",
                f"order=generated_at.desc",
                f"select=id,importance,insight_type,confidence,generated_at,title",
            ],
        )
        headers = self._get_headers()
        headers["Prefer"] = "count=none"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Error fetching insights for entity {entity_id}: {e}")
        return []

    async def _fetch_entities(self) -> List[Dict]:
        """Fetch all non-archived entities for the user."""
        url = self.supabase.build_url(
            "entities",
            [
                f"user_id=eq.{self.user_id}",
                "is_archived=eq.false",
                "select=id,name,industry",
            ],
        )
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Error fetching entities: {e}")
        return []

    def _compute_entity_momentum(
        self,
        entity: Dict,
        insights_this_week: int,
        insights_last_week: int,
    ) -> MomentumEntity:
        """Compute momentum metrics for a single entity."""
        # Velocity: % change WoW
        if insights_last_week > 0:
            velocity_percent = ((insights_this_week - insights_last_week) / insights_last_week) * 100
        elif insights_this_week > 0:
            velocity_percent = 100.0  # From 0 to something = 100% velocity
        else:
            velocity_percent = 0.0

        # Momentum score: weighted velocity, clamped to -100 to +100
        # Higher weight for more weeks with activity
        weeks_active = (1 if insights_this_week > 0 else 0) + (1 if insights_last_week > 0 else 0)
        if weeks_active >= 1:
            momentum_score = max(-100, min(100, velocity_percent))
        else:
            momentum_score = 0.0

        # Direction
        if momentum_score > ACCELERATING_THRESHOLD:
            direction = MomentumDirection.ACCELERATING
        elif momentum_score < DECELERATING_THRESHOLD:
            direction = MomentumDirection.DECELERATING
        else:
            direction = MomentumDirection.STABLE

        return MomentumEntity(
            entity_id=entity["id"],
            entity_name=entity["name"],
            industry=entity.get("industry"),
            insights_this_week=insights_this_week,
            insights_last_week=insights_last_week,
            velocity_percent=round(velocity_percent, 1),
            momentum_score=round(momentum_score, 1),
            direction=direction,
        )

    async def compute_momentum_stats(self) -> MomentumStats:
        """
        Compute full momentum statistics for all tracked entities.

        Returns:
            MomentumStats with top movers, decelerators, most active, and industry heat.
        """
        entities = await self._fetch_entities()
        if not entities:
            return MomentumStats()

        entity_momentum_list: List[MomentumEntity] = []
        total_insights_this_week = 0
        total_insights_last_week = 0

        for entity in entities:
            # Fetch for this week (7 days) and last week (7-14 days ago)
            insights_this_week_data = await self._fetch_insights_for_entity(entity["id"], days=7)
            insights_last_week_data = await self._fetch_insights_for_entity(entity["id"], days=14)

            # Count last week's insights (8-14 days ago)
            last_week_start = (datetime.utcnow() - timedelta(days=14)).isoformat()
            last_week_data = [
                i for i in insights_last_week_data
                if i.get("generated_at", "") < last_week_start
            ]

            insights_this_week = len(insights_this_week_data)
            insights_last_week = len(last_week_data)

            total_insights_this_week += insights_this_week
            total_insights_last_week += insights_last_week

            momentum_entity = self._compute_entity_momentum(
                entity, insights_this_week, insights_last_week
            )

            # Attach top insight types if we have insights
            if insights_this_week_data:
                type_counts = Counter(i.get("insight_type", "summary") for i in insights_this_week_data)
                top_types = [t for t, _ in type_counts.most_common(3)]
                momentum_entity.top_insight_types = top_types

                # Average importance
                importance_counts = Counter(
                    i.get("importance", "low") for i in insights_this_week_data
                )
                # Weighted average
                total_weight = sum(
                    IMPORTANCE_WEIGHTS.get(imp, 1) * count
                    for imp, count in importance_counts.items()
                )
                total_count = sum(importance_counts.values())
                if total_count > 0:
                    weighted_avg = total_weight / total_count
                    if weighted_avg >= 3.5:
                        momentum_entity.avg_importance = "critical"
                    elif weighted_avg >= 2.5:
                        momentum_entity.avg_importance = "high"
                    elif weighted_avg >= 1.5:
                        momentum_entity.avg_importance = "medium"
                    else:
                        momentum_entity.avg_importance = "low"

            entity_momentum_list.append(momentum_entity)

        # Sort by momentum score
        sorted_by_momentum = sorted(
            entity_momentum_list,
            key=lambda x: x.momentum_score,
            reverse=True,
        )

        # Sort by raw volume
        sorted_by_volume = sorted(
            entity_momentum_list,
            key=lambda x: x.insights_this_week,
            reverse=True,
        )

        # Industry heat index: total signals this week
        if total_insights_last_week > 0:
            industry_heat_change = (
                (total_insights_this_week - total_insights_last_week)
                / total_insights_last_week
            ) * 100
        elif total_insights_this_week > 0:
            industry_heat_change = 100.0
        else:
            industry_heat_change = 0.0

        return MomentumStats(
            top_movers=sorted_by_momentum[:5],
            top_decelerators=sorted_by_momentum[-5:] if len(sorted_by_momentum) >= 5 else sorted_by_momentum,
            most_active=sorted_by_volume[:5],
            industry_heat_index=total_insights_this_week,
            industry_heat_change=round(industry_heat_change, 1),
            total_entities_tracked=len(entities),
        )
