"""
Anomaly Detection Engine — Pillar 2 of the 4-Pillar Intelligence Strategy.

Detects when a competitor does something UNUSUAL for THEM specifically —
not keywords, but deviation from their own historical baseline.

Example:
- Tesla NEVER posts jobs → suddenly 20 new postings → anomaly
- Company that generates 2 articles/week → 25 articles in one week → anomaly
- Industry that usually has steady signal → sudden spike → anomaly
"""
import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

import httpx
from app.api.deps import get_supabase, SupabaseClient
from app.schemas.entity import InsightImportance

logger = logging.getLogger(__name__)

# Statistical thresholds
ANOMALY_DEVIATION_THRESHOLD = 2.0  # Standard deviations above baseline to trigger
ANOMALY_WINDOW_DAYS = 30  # Rolling window for baseline
ANOMALY_LOOKBACK_WEEKS = 12  # Weeks of data for baseline computation
MIN_WEEKS_NEEDED = 3  # Minimum historical data before anomaly detection is meaningful


class AnomalyDetector:
    """Statistical anomaly detection for entity signal patterns."""

    def __init__(self, user_id: str, supabase: Optional[SupabaseClient] = None):
        self.user_id = user_id
        self.supabase = supabase or get_supabase()

    def _get_headers(self) -> dict:
        return self.supabase._get_headers()

    async def _fetch_weekly_insights(
        self, entity_id: str, weeks_back: int = ANOMALY_LOOKBACK_WEEKS
    ) -> List[Dict]:
        """Fetch insights grouped by week for anomaly analysis."""
        url = f"{self.supabase.url}/rest/v1/insights"
        headers = self._get_headers()
        headers["Prefer"] = "count=none"

        # Fetch last N weeks of data
        from_date = (datetime.utcnow() - timedelta(weeks=weeks_back)).isoformat()
        params = [
            f"user_id=eq.{self.user_id}",
            f"entity_id=eq.{entity_id}",
            f"is_archived=eq.false",
            f"generated_at=gte.{from_date}",
            "order=generated_at.asc",
        ]
        pairs = [(p.split("=", 1)[0], p.split("=", 1)[1]) for p in params if "=" in p]
        import urllib.parse
        query = urllib.parse.urlencode(pairs)
        url_with_params = f"{url}?{query}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url_with_params, headers=headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Error fetching weekly insights for {entity_id}: {e}")
        return []

    def _compute_weekly_counts(self, insights: List[Dict]) -> List[Tuple[str, int]]:
        """Group insights by week (ISO week number) and count them."""
        weekly_counts: Dict[str, List[int]] = defaultdict(list)

        for insight in insights:
            try:
                generated_at = insight.get("generated_at", "")
                if generated_at:
                    dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                    week_key = dt.strftime("%Y-W%W")  # e.g. "2026-W11"
                    importance = insight.get("importance", "low")
                    weight = {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(importance, 1)
                    weekly_counts[week_key].append(weight)
            except (ValueError, TypeError):
                continue

        # Sum weights per week
        return [(week, sum(weights)) for week, weights in sorted(weekly_counts.items())]

    def _compute_baseline(self, weekly_counts: List[Tuple[str, int]]) -> Tuple[float, float, float]:
        """
        Compute baseline statistics from weekly counts.

        Returns:
            (mean, std_dev, count) — mean and standard deviation of weekly insight counts
        """
        if len(weekly_counts) < MIN_WEEKS_NEEDED:
            return 0.0, 0.0, len(weekly_counts)

        counts = [count for _, count in weekly_counts]
        n = len(counts)
        mean = sum(counts) / n
        variance = sum((c - mean) ** 2 for c in counts) / n
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        return mean, std_dev, n

    def _detect_anomaly(
        self,
        weekly_counts: List[Tuple[str, int]],
        mean: float,
        std_dev: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if the most recent week is anomalous compared to baseline.

        Returns anomaly info if detected, None otherwise.
        """
        if len(weekly_counts) < 1 or std_dev == 0:
            return None

        # Check the most recent week's count
        most_recent_week, most_recent_count = weekly_counts[-1]
        mean_prev = mean  # baseline from prior weeks

        if mean_prev == 0:
            if most_recent_count > 0:
                # First week with signals is always a spike from nothing
                return None
            return None

        deviation = (most_recent_count - mean_prev) / std_dev

        if deviation > ANOMALY_DEVIATION_THRESHOLD:
            direction = "surge" if most_recent_count > mean_prev else "drop"
            return {
                "deviation": round(deviation, 2),
                "direction": direction,
                "most_recent_week": most_recent_week,
                "most_recent_count": most_recent_count,
                "baseline_mean": round(mean_prev, 2),
                "baseline_stddev": round(std_dev, 2),
            }

        return None

    def _build_anomaly_insight(
        self,
        entity_name: str,
        anomaly_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build an anomaly insight from detected anomaly data."""
        direction = anomaly_info["direction"]
        deviation = anomaly_info["deviation"]
        week = anomaly_info["most_recent_week"]
        count = anomaly_info["most_recent_count"]
        baseline = anomaly_info["baseline_mean"]
        stddev = anomaly_info["baseline_stddev"]

        if direction == "surge":
            title = f"Unusual signal surge for {entity_name}"
            content = (
                f"{entity_name} generated {count} signals in week {week} "
                f"(baseline: {baseline:.1f} ± {stddev:.1f}). "
                f"This is {deviation:.1f}σ above their normal activity — significantly more news/activity than usual. "
                f"Could indicate: announcement, crisis, major development, or unusual activity worth monitoring."
            )
            importance = "high"
        else:
            title = f"Unusual signal drop for {entity_name}"
            content = (
                f"{entity_name} generated only {count} signals in week {week} "
                f"(baseline: {baseline:.1f} ± {stddev:.1f}). "
                f"This is {deviation:.1f}σ below their normal activity — significantly quieter than usual. "
                f"Could indicate: reduced activity, data source issue, or strategic silence."
            )
            importance = "medium"

        return {
            "insight_type": "anomaly",
            "title": title,
            "content": content,
            "importance": importance,
            "confidence": 0.75,
            "metadata": {
                "anomaly_direction": direction,
                "deviation_sigma": deviation,
                "week": week,
                "signal_count": count,
                "baseline_mean": baseline,
                "baseline_stddev": stddev,
            },
        }

    async def check_entity_anomaly(
        self, entity_id: str, entity_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Check if an entity has anomalous activity this week.

        Returns an anomaly insight dict if detected, None otherwise.
        """
        try:
            insights = await self._fetch_weekly_insights(entity_id)
            if not insights:
                return None

            weekly_counts = self._compute_weekly_counts(insights)
            mean, std_dev, n_weeks = self._compute_baseline(weekly_counts)

            if n_weeks < MIN_WEEKS_NEEDED:
                logger.debug(
                    f"Skipping anomaly detection for {entity_name}: "
                    f"only {n_weeks} weeks of data (need {MIN_WEEKS_NEEDED})"
                )
                return None

            anomaly_info = self._detect_anomaly(weekly_counts, mean, std_dev)
            if anomaly_info:
                return self._build_anomaly_insight(entity_name, anomaly_info)

            return None

        except Exception as e:
            logger.error(f"Anomaly detection error for {entity_id}: {e}")
            return None


async def run_anomaly_detection(
    user_id: str,
    entities: List[Dict],
    supabase: Optional[SupabaseClient] = None,
) -> List[Dict]:
    """
    Run anomaly detection for all entities.

    Returns list of anomaly insights (one per entity with anomaly).
    """
    detector = AnomalyDetector(user_id=user_id, supabase=supabase)
    anomaly_insights: List[Dict] = []

    for entity in entities:
        anomaly = await detector.check_entity_anomaly(
            entity["id"], entity.get("name", "Unknown Entity")
        )
        if anomaly:
            anomaly["entity_id"] = entity["id"]
            anomaly["user_id"] = user_id
            anomaly["entity_name"] = entity.get("name")
            anomaly_insights.append(anomaly)

    return anomaly_insights
