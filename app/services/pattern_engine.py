"""
Predictive Pattern Engine — Pillar 4 of the 4-Pillar Intelligence Strategy.

Learns historical patterns from tracked insights and generates predictions:
"Based on past patterns: Company X tends to [do B] 14-21 days after [doing A]."

Example patterns:
- Funding announced → Hiring spike in 14-21 days
- Product launch → PR/media coverage in 3-7 days
- Leadership change → Strategy shift signals in 30-60 days
"""
import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import defaultdict

import httpx
from app.api.deps import get_supabase, SupabaseClient

logger = logging.getLogger(__name__)

# Pattern detection config
MIN_OBSERVATIONS = 2          # Minimum times a pattern must occur to be stored
MIN_CONFIDENCE = 0.5          # Minimum confidence to generate a prediction
LOOKBACK_WEEKS = 12           # How far back to look for pattern detection
PATTERN_LAG_DAYS_MIN = 3      # Minimum lag between antecedent → consequent
PATTERN_LAG_DAYS_MAX = 90     # Maximum lag to consider

# Insight types that are meaningful for pattern detection
PATTERN_RELEVANT_TYPES = {
    "funding", "product", "hiring", "leadership",
    "partnership", "pr", "anomaly", "summary"
}


class PatternEngine:
    """Learns and stores patterns from insight history."""

    def __init__(self, user_id: str, supabase: Optional[SupabaseClient] = None):
        self.user_id = user_id
        self.supabase = supabase or get_supabase()

    def _get_headers(self) -> dict:
        return self.supabase._get_headers()

    async def _fetch_entity_insights(
        self, entity_id: str, weeks_back: int = LOOKBACK_WEEKS
    ) -> List[Dict]:
        """Fetch insights for pattern learning."""
        from_date = (datetime.utcnow() - timedelta(weeks=weeks_back)).isoformat()
        url = self.supabase.build_url(
            "insights",
            [
                f"user_id=eq.{self.user_id}",
                f"entity_id=eq.{entity_id}",
                f"is_archived=eq.false",
                f"generated_at=gte.{from_date}",
                f"order=generated_at.asc",
                f"select=id,insight_type,importance,generated_at,title",
            ],
        )
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Error fetching insights for pattern learning: {e}")
        return []

    def _detect_patterns_in_timeline(
        self, insights: List[Dict]
    ) -> List[Dict]:
        """
        Detect antecedent → consequent patterns in an insight timeline.

        Looks for: insight_type A followed by insight_type B within N days.
        """
        patterns: Dict[str, Dict] = defaultdict(lambda: {
            "observed_count": 0,
            "lag_days": [],
            "first_seen": None,
            "last_seen": None,
        })

        # Group insights by type
        by_type: Dict[str, List[Dict]] = defaultdict(list)
        for insight in insights:
            itype = insight.get("insight_type", "summary")
            if itype in PATTERN_RELEVANT_TYPES:
                by_type[itype].append(insight)

        # For each pair of types, check if A consistently precedes B
        type_list = list(PATTERN_RELEVANT_TYPES)
        for i, antecedent_type in enumerate(type_list):
            for consequent_type in type_list[i + 1:]:
                a_insights = by_type.get(antecedent_type, [])
                b_insights = by_type.get(consequent_type, [])

                if not a_insights or not b_insights:
                    continue

                # Check each A → subsequent Bs
                for a in a_insights:
                    try:
                        a_dt = datetime.fromisoformat(
                            a.get("generated_at", "").replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        continue

                    for b in b_insights:
                        try:
                            b_dt = datetime.fromisoformat(
                                b.get("generated_at", "").replace("Z", "+00:00")
                            )
                        except (ValueError, TypeError):
                            continue

                        lag = (b_dt - a_dt).days

                        if PATTERN_LAG_DAYS_MIN <= lag <= PATTERN_LAG_DAYS_MAX:
                            key = f"{antecedent_type}→{consequent_type}"
                            p = patterns[key]
                            p["observed_count"] += 1
                            p["lag_days"].append(lag)
                            if p["first_seen"] is None:
                                p["first_seen"] = a_dt.isoformat()
                            p["last_seen"] = b_dt.isoformat()
                            break  # Only count first B after each A

        return patterns

    def _compute_pattern_confidence(
        self, patterns: Dict[str, Dict], total_insights: int
    ) -> List[Dict]:
        """Compute confidence scores for detected patterns."""
        validated = []

        for pattern_key, data in patterns.items():
            count = data["observed_count"]
            if count < MIN_OBSERVATIONS:
                continue

            # Confidence = how consistently this pattern occurs
            # vs how many opportunities there were
            # Simple: count / total_relevant_pairs (approximate)
            confidence = min(count / (MIN_OBSERVATIONS + count * 0.5), 0.95)

            if confidence < MIN_CONFIDENCE:
                continue

            lag_list = data["lag_days"]
            avg_lag = sum(lag_list) / len(lag_list)
            min_lag = min(lag_list)
            max_lag = max(lag_list)

            antecedent, consequent = pattern_key.split("→")

            validated.append({
                "antecedent_event": antecedent,
                "consequent_event": consequent,
                "observed_count": count,
                "confidence_score": round(confidence, 3),
                "typical_lag_days": round(avg_lag),
                "lag_window_min": min_lag,
                "lag_window_max": max_lag,
                "first_seen": data["first_seen"],
                "last_observed_at": data["last_seen"],
            })

        return validated

    async def learn_entity_patterns(
        self, entity_id: str
    ) -> List[Dict]:
        """
        Learn patterns from an entity's insight history and store them.

        Returns list of newly learned or updated patterns.
        """
        insights = await self._fetch_entity_insights(entity_id)
        if len(insights) < 4:  # Need enough data to detect patterns
            return []

        raw_patterns = self._detect_patterns_in_timeline(insights)
        validated_patterns = self._compute_pattern_confidence(
            raw_patterns, len(insights)
        )

        if not validated_patterns:
            return []

        # Store/update patterns in database
        stored_patterns = []
        headers = self._get_headers()
        headers["Prefer"] = "return=representation"

        for pattern in validated_patterns:
            # Check if pattern already exists
            check_url = self.supabase.build_url(
                "entity_patterns",
                [
                    f"entity_id=eq.{entity_id}",
                    f"user_id=eq.{self.user_id}",
                    f"antecedent_event=eq.{pattern['antecedent_event']}",
                    f"consequent_event=eq.{pattern['consequent_event']}",
                    "select=id,observed_count,confidence_score",
                ],
            )

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    check_resp = await client.get(check_url, headers=headers)

                    if check_resp.status_code == 200 and check_resp.json():
                        # Update existing pattern
                        existing = check_resp.json()[0]
                        new_count = existing["observed_count"] + pattern["observed_count"]
                        # Re-compute confidence with more data
                        new_conf = min(
                            new_count / (MIN_OBSERVATIONS + new_count * 0.5),
                            0.95
                        )

                        update_url = self.supabase.build_url(
                            "entity_patterns",
                            [f"id=eq.{existing['id']}"],
                        )
                        update_data = {
                            "observed_count": new_count,
                            "confidence_score": round(new_conf, 3),
                            "typical_lag_days": pattern["typical_lag_days"],
                            "last_observed_at": pattern["last_observed_at"],
                        }
                        await client.patch(update_url, json=update_data, headers=headers)
                        pattern["id"] = existing["id"]
                    else:
                        # Insert new pattern
                        insert_url = f"{self.supabase.url}/rest/v1/entity_patterns"
                        insert_data = {
                            "entity_id": entity_id,
                            "user_id": self.user_id,
                            "antecedent_event": pattern["antecedent_event"],
                            "consequent_event": pattern["consequent_event"],
                            "typical_lag_days": pattern["typical_lag_days"],
                            "confidence_score": pattern["confidence_score"],
                            "observed_count": pattern["observed_count"],
                            "last_observed_at": pattern["last_observed_at"],
                        }
                        insert_resp = await client.post(
                            insert_url, json=insert_data, headers=headers
                        )
                        if insert_resp.status_code in [200, 201]:
                            pattern["id"] = insert_resp.json()[0].get("id")

                    stored_patterns.append(pattern)

            except Exception as e:
                logger.error(f"Error storing pattern: {e}")
                continue

        return stored_patterns

    async def get_active_predictions(
        self, entity_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all active learned patterns that can generate predictions.

        A pattern is "active" if its last observed date is within
        the typical lag window (suggesting it could trigger again).
        """
        url = self.supabase.build_url(
            "entity_patterns",
            [
                f"user_id=eq.{self.user_id}",
                f"confidence_score=gte.{MIN_CONFIDENCE}",
                "order=confidence_score.desc",
                f"select=id,entity_id,antecedent_event,consequent_event,"
                "typical_lag_days,confidence_score,observed_count,last_observed_at",
            ],
        )
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    patterns = response.json()
                    if entity_id:
                        patterns = [p for p in patterns if p.get("entity_id") == entity_id]
                    return patterns
        except Exception as e:
            logger.error(f"Error fetching active patterns: {e}")
        return []


class PredictionEngine:
    """
    Generates natural language predictions from learned patterns.

    Uses the existing Claude API (via ContentAnalyzer) to generate
    human-readable predictions from pattern data.
    """

    def __init__(self, user_id: str, supabase: Optional[SupabaseClient] = None):
        self.user_id = user_id
        self.supabase = supabase or get_supabase()
        self.pattern_engine = PatternEngine(user_id, supabase)

    async def _fetch_entity_name(self, entity_id: str) -> str:
        """Fetch entity name for human-readable predictions."""
        url = self.supabase.build_url(
            "entities",
            [f"id=eq.{entity_id}", "select=name"],
        )
        headers = self.pattern_engine._get_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200 and response.json():
                    return response.json()[0].get("name", "Unknown Entity")
        except Exception:
            pass
        return "Unknown Entity"

    def _build_prediction_prompt(
        self,
        entity_name: str,
        entity_id: str,
        patterns: List[Dict],
    ) -> str:
        """Build prompt for Claude to generate predictions."""

        pattern_lines = []
        for p in patterns:
            antecedent = p["antecedent_event"].replace("_", " ")
            consequent = p["consequent_event"].replace("_", " ")
            confidence = int(p["confidence_score"] * 100)
            lag = p["typical_lag_days"]
            observations = p["observed_count"]

            pattern_lines.append(
                f"- Based on {observations} observations: "
                f"'{antecedent}' typically leads to '{consequent}' "
                f"within {lag} days (confidence: {confidence}%)."
            )

        patterns_text = "\n".join(pattern_lines) if pattern_lines else "No strong patterns detected yet."

        return f"""You are a competitive intelligence analyst. Generate forward-looking predictions for {entity_name} based on their historical patterns.

DETECTED PATTERNS:
{patterns_text}

CONTEXT:
- Entity: {entity_name}
- You are analyzing {len(patterns)} active pattern(s) with historical data

TASK:
Generate 1-3 natural language predictions for what {entity_name} is likely to do next, based on their patterns.

IMPORTANT RULES:
- Only predict if confidence >= 50%
- Be specific about timing (e.g. "in the next 2-3 weeks")
- Ground predictions in the actual observed patterns
- If no strong patterns exist, say "Not enough data to generate reliable predictions"
- Format each prediction with: [PREDICTION] then 1-2 sentences explaining the reasoning

Respond with ONLY valid JSON (no markdown):
{{
    "predictions": [
        {{
            "title": "Brief prediction title",
            "content": "2-3 sentence explanation of what to expect and why",
            "confidence": 0.0-1.0,
            "predicted_window_days": number,
            "importance": "high|medium|low"
        }}
    ]
}}"""

    async def generate_predictions(
        self, entity_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Generate prediction insights for an entity or all user entities.

        Returns list of prediction insight dicts ready to be stored.
        """
        from app.services.pipeline import ContentAnalyzer

        patterns = await self.pattern_engine.get_active_predictions(entity_id)
        if not patterns:
            return []

        # Group patterns by entity
        patterns_by_entity: Dict[str, List[Dict]] = defaultdict(list)
        for p in patterns:
            patterns_by_entity[p["entity_id"]].append(p)

        all_predictions: List[Dict] = []

        for eid, entity_patterns in patterns_by_entity.items():
            entity_name = await self._fetch_entity_name(eid)

            # Use ContentAnalyzer (Claude) to generate natural language predictions
            analyzer = ContentAnalyzer()
            prompt = self._build_prediction_prompt(entity_name, eid, entity_patterns)

            try:
                # Use the analyzer's API call logic
                import json as json_module
                from app.core.config import settings

                api_key = settings.ANTHROPIC_API_KEY
                if not api_key:
                    # Fallback: generate simple predictions without Claude
                    predictions = self._generate_fallback_predictions(
                        entity_name, entity_patterns
                    )
                else:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": "claude-3-5-haiku-20241022",
                                "max_tokens": 1024,
                                "messages": [{"role": "user", "content": prompt}]
                            }
                        )

                    if resp.status_code == 200:
                        text = resp.json()["content"][0]["text"]
                        data = json_module.loads(text)
                        predictions = data.get("predictions", [])
                    else:
                        predictions = self._generate_fallback_predictions(
                            entity_name, entity_patterns
                        )

                for pred in predictions:
                    all_predictions.append({
                        "entity_id": eid,
                        "entity_name": entity_name,
                        "insight_type": "prediction",
                        "title": pred.get("title", "Pattern-Based Prediction"),
                        "content": pred.get("content", ""),
                        "confidence": pred.get("confidence", 0.5),
                        "importance": pred.get("importance", "medium"),
                        "predicted_window_days": pred.get("predicted_window_days", 14),
                        "antecedent_event": entity_patterns[0]["antecedent_event"],
                        "consequent_event": entity_patterns[0]["consequent_event"],
                        "generated_at": datetime.utcnow().isoformat(),
                    })

            except Exception as e:
                logger.error(f"Error generating prediction for {entity_name}: {e}")
                # Fallback
                predictions = self._generate_fallback_predictions(
                    entity_name, entity_patterns
                )
                for pred in predictions:
                    all_predictions.append({
                        "entity_id": eid,
                        "entity_name": entity_name,
                        "insight_type": "prediction",
                        "title": pred["title"],
                        "content": pred["content"],
                        "confidence": pred.get("confidence", 0.5),
                        "importance": pred.get("importance", "medium"),
                        "generated_at": datetime.utcnow().isoformat(),
                    })

        return all_predictions

    def _generate_fallback_predictions(
        self, entity_name: str, patterns: List[Dict]
    ) -> List[Dict]:
        """Generate simple predictions without Claude API."""
        predictions = []

        for p in patterns[:3]:  # Top 3 patterns
            antecedent = p["antecedent_event"].replace("_", " ")
            consequent = p["consequent_event"].replace("_", " ")
            lag = p["typical_lag_days"]
            confidence = p["confidence_score"]

            predictions.append({
                "title": f"{entity_name}: Expect {consequent} soon",
                "content": (
                    f"Based on {p['observed_count']} observed occurrences: "
                    f"when {entity_name} shows '{antecedent}', "
                    f"'{consequent}' typically follows within {lag} days "
                    f"(confidence: {int(confidence * 100)}%)."
                ),
                "confidence": confidence,
                "importance": "high" if confidence > 0.7 else "medium",
                "predicted_window_days": lag,
            })

        return predictions


async def run_prediction_generation(
    user_id: str,
    entity_id: Optional[str] = None,
    supabase: Optional[SupabaseClient] = None,
) -> List[Dict]:
    """
    Main entry point: learn patterns and generate predictions.

    Call this after the pipeline runs to update pattern knowledge
    and generate new prediction insights.
    """
    engine = PredictionEngine(user_id, supabase)

    # First, learn patterns (only if entity_id specified, or all entities)
    if entity_id:
        await engine.pattern_engine.learn_entity_patterns(entity_id)
    else:
        # Learn patterns for all user entities
        from app.api.deps import get_supabase
        sb = supabase or get_supabase()
        url = sb.build_url(
            "entities",
            [f"user_id=eq.{user_id}", "is_archived=eq.false", "select=id"],
        )
        headers = sb._get_headers()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    entities = resp.json()
                    for entity in entities:
                        await engine.pattern_engine.learn_entity_patterns(entity["id"])
        except Exception as e:
            logger.error(f"Error learning patterns: {e}")

    # Then generate predictions
    predictions = await engine.generate_predictions(entity_id)
    return predictions
