"""
Competitive Benchmarking Engine — Pillar 3 of the 4-Pillar Intelligence Strategy.

Shows WHERE competitors stand RELATIVE to each other — market share of attention,
competitive delta, and industry rank.

Supports TWO grouping modes:
1. AUTO by industry field (no setup needed)
2. MANUAL user-created competitive groups
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import defaultdict

import httpx
from app.api.deps import get_supabase, SupabaseClient
from app.schemas.entity import (
    CompetitiveStats,
    CompetitiveEntity,
    CompetitiveGroupStats,
    InsightImportance,
)

logger = logging.getLogger(__name__)

# Importance weights for weighted signals
IMPORTANCE_WEIGHTS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
    None: 1.0,
}


class BenchmarkEngine:
    """Computes competitive benchmarks across industry groups and manual groups."""

    def __init__(self, user_id: str, supabase: Optional[SupabaseClient] = None):
        self.user_id = user_id
        self.supabase = supabase or get_supabase()

    def _get_headers(self) -> dict:
        return self.supabase._get_headers()

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
                f"select=id,importance,insight_type,confidence",
            ],
        )
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Error fetching insights for {entity_id}: {e}")
        return []

    async def _fetch_manual_groups(self) -> List[Dict]:
        """Fetch user's manual competitive groups with entity IDs."""
        url = self.supabase.build_url(
            "competitive_groups",
            [
                f"user_id=eq.{self.user_id}",
                "select=id,name",
            ],
        )
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    groups = response.json()
                    if not groups:
                        return []
                    # Fetch group entity mappings
                    group_ids = [g["id"] for g in groups]
                    entities_url = self.supabase.build_url(
                        "competitive_group_entities",
                        [
                            f"group_id=in.({','.join(group_ids)})",
                            "select=group_id,entity_id",
                        ],
                    )
                    rel_response = await client.get(entities_url, headers=headers)
                    if rel_response.status_code == 200:
                        relations = rel_response.json()
                        # Map group_id → list of entity_ids
                        group_entities: Dict[str, List[str]] = defaultdict(list)
                        for rel in relations:
                            group_entities[rel["group_id"]].append(rel["entity_id"])
                        for g in groups:
                            g["entity_ids"] = group_entities.get(g["id"], [])
                    return groups
        except Exception as e:
            logger.error(f"Error fetching manual groups: {e}")
        return []

    async def _compute_entity_signals(
        self, entity_id: str
    ) -> tuple[int, int, float]:
        """
        Compute weighted signal counts for an entity.
        Returns (signals_this_week, signals_last_week, weighted_score_this_week).
        """
        insights_tw = await self._fetch_insights_for_entity(entity_id, days=7)
        insights_lw = await self._fetch_insights_for_entity(entity_id, days=14)

        # Count last week's signals (8-14 days ago)
        lw_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        insights_lw_actual = [i for i in insights_lw if i.get("generated_at", "") < lw_cutoff]

        # Weighted score (importance-weighted count)
        def weighted_score(insights: List[Dict]) -> float:
            return sum(IMPORTANCE_WEIGHTS.get(i.get("importance"), 1) for i in insights)

        return (
            len(insights_tw),
            len(insights_lw_actual),
            weighted_score(insights_tw),
        )

    def _compute_group_benchmarks(
        self,
        group_name: str,
        group_type: str,
        entities_in_group: List[Dict],
        entity_signals: Dict[str, tuple[int, int, float]],
    ) -> CompetitiveGroupStats:
        """Compute group-level statistics."""
        total_signals_tw = sum(
            entity_signals.get(e["id"], (0, 0, 0))[0] for e in entities_in_group
        )
        total_signals_lw = sum(
            entity_signals.get(e["id"], (0, 0, 0))[1] for e in entities_in_group
        )

        if total_signals_tw > 0 and total_signals_lw > 0:
            heat_change = ((total_signals_tw - total_signals_lw) / total_signals_lw) * 100
        elif total_signals_tw > 0:
            heat_change = 100.0
        else:
            heat_change = 0.0

        # Top player and fastest rising
        top_player = None
        fastest_rising = None
        max_share = -1
        max_delta = -9999

        for e in entities_in_group:
            tw, lw, _ = entity_signals.get(e["id"], (0, 0, 0.0))
            share = tw / total_signals_tw if total_signals_tw > 0 else 0
            if share > max_share:
                max_share = share
                top_player = e["name"]
            lw_share = lw / total_signals_lw if total_signals_lw > 0 else 0
            delta = share - lw_share
            if delta > max_delta:
                max_delta = delta
                fastest_rising = e["name"]

        return CompetitiveGroupStats(
            group_name=group_name,
            group_type=group_type,
            total_entities=len(entities_in_group),
            total_signals_this_week=total_signals_tw,
            total_signals_last_week=total_signals_lw,
            industry_heat_index=total_signals_tw,
            heat_change_percent=round(heat_change, 1),
            top_player=top_player,
            fastest_rising=fastest_rising,
        )

    async def compute_competitive_stats(self) -> CompetitiveStats:
        """
        Compute full competitive benchmarking stats.

        Returns:
            CompetitiveStats with industry benchmarks, manual group benchmarks,
            and top entities ranked by share of attention.
        """
        entities = await self._fetch_entities()
        if not entities:
            return CompetitiveStats()

        manual_groups = await self._fetch_manual_groups()

        # Pre-compute signals for all entities (avoid duplicate fetches)
        entity_signals: Dict[str, tuple[int, int, float]] = {}
        for entity in entities:
            signals = await self._compute_entity_signals(entity["id"])
            entity_signals[entity["id"]] = signals

        # ===== AUTO: Group by industry =====
        industry_groups: Dict[str, List[Dict]] = defaultdict(list)
        for entity in entities:
            industry = entity.get("industry") or "Other"
            industry_groups[industry].append(entity)

        industry_benchmarks: List[CompetitiveGroupStats] = []
        all_competitive_entities: List[CompetitiveEntity] = []

        for industry, group_entities in industry_groups.items():
            if len(group_entities) < 2:
                # Skip single-entity industries — no meaningful comparison
                continue

            # Compute share + delta for each entity
            group_signals_tw = sum(
                entity_signals.get(e["id"], (0, 0, 0))[0] for e in group_entities
            )

            entities_with_stats: List[CompetitiveEntity] = []
            for e in group_entities:
                tw, lw, _ = entity_signals.get(e["id"], (0, 0, 0.0))
                share = (tw / group_signals_tw) * 100 if group_signals_tw > 0 else 0.0
                lw_group_signals = sum(
                    entity_signals.get(ee["id"], (0, 0, 0))[1] for ee in group_entities
                )
                lw_share = (lw / lw_group_signals * 100) if lw_group_signals > 0 else 0.0
                delta = share - lw_share

                entities_with_stats.append(
                    CompetitiveEntity(
                        entity_id=e["id"],
                        entity_name=e["name"],
                        industry=industry,
                        insights_this_week=tw,
                        insights_last_week=lw,
                        share_of_attention=round(share, 2),
                        competitive_delta=round(delta, 2),
                        rank_in_group=0,  # filled below
                        group_name=industry,
                        group_type="industry",
                    )
                )

            # Sort by share to assign rank
            entities_with_stats.sort(key=lambda x: x.share_of_attention, reverse=True)
            for rank, ce in enumerate(entities_with_stats, 1):
                ce.rank_in_group = rank

            all_competitive_entities.extend(entities_with_stats)

            # Group stats
            group_stats = self._compute_group_benchmarks(
                industry, "industry", group_entities, entity_signals
            )
            industry_benchmarks.append(group_stats)

        # ===== MANUAL: Competitive groups =====
        manual_group_stats: List[CompetitiveGroupStats] = []
        for group in manual_groups:
            entity_ids = group.get("entity_ids", [])
            group_entities = [e for e in entities if e["id"] in entity_ids]

            if len(group_entities) < 2:
                continue

            # Compute share + delta for each entity
            group_signals_tw = sum(
                entity_signals.get(e["id"], (0, 0, 0))[0] for e in group_entities
            )

            for e in group_entities:
                tw, lw, _ = entity_signals.get(e["id"], (0, 0, 0.0))
                share = (tw / group_signals_tw) * 100 if group_signals_tw > 0 else 0.0
                lw_group_signals = sum(
                    entity_signals.get(ee["id"], (0, 0, 0))[1] for ee in group_entities
                )
                lw_share = (lw / lw_group_signals * 100) if lw_group_signals > 0 else 0.0
                delta = share - lw_share

                all_competitive_entities.append(
                    CompetitiveEntity(
                        entity_id=e["id"],
                        entity_name=e["name"],
                        industry=e.get("industry"),
                        insights_this_week=tw,
                        insights_last_week=lw,
                        share_of_attention=round(share, 2),
                        competitive_delta=round(delta, 2),
                        rank_in_group=0,
                        group_name=group["name"],
                        group_type="manual",
                    )
                )

            group_stats = self._compute_group_benchmarks(
                group["name"], "manual", group_entities, entity_signals
            )
            manual_group_stats.append(group_stats)

        # Sort all entities by share of attention (top across all groups)
        all_competitive_entities.sort(key=lambda x: x.share_of_attention, reverse=True)

        return CompetitiveStats(
            industry_benchmarks=industry_benchmarks,
            manual_groups=manual_group_stats,
            top_entities=all_competitive_entities[:10],  # Top 10 across all groups
            total_industries_tracked=len(industry_benchmarks),
            total_groups=len(industry_benchmarks) + len(manual_group_stats),
        )
