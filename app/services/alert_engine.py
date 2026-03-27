"""
Alert Engine — real-time score change detection and alert firing.

Runs on APScheduler every 6 hours. Detects:
  - Tier changes: cold→warm, warm→hot (tier_up), hot→warm, warm→cold (tier_down)
  - Score spikes: +20 pts or more since last alert cycle

Sends in-app alerts (stored in alerts table) and email notifications via Resend.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..core.config import settings
from ..schemas.prospect import TIER_HOT, TIER_WARM
from .email_alert_service import send_score_alert_email

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------- #
# Helpers
# -------------------------------------------------------------------------- #

def _service_headers() -> dict:
    """Headers for service-role Supabase requests (bypasses RLS)."""
    return {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _tier_from_score(score: float) -> str:
    """Map a numeric score to its tier label."""
    if score >= TIER_HOT:
        return "hot"
    if score >= TIER_WARM:
        return "warm"
    return "cold"


async def _supabase_get(url: str) -> list:
    """GET a Supabase REST endpoint, return parsed JSON list. [] on failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=_service_headers())
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        logger.warning(f"ALERT ENGINE: GET {url} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"ALERT ENGINE: GET {url} failed: {e}")
    return []


async def _supabase_post(url: str, json_body: dict) -> Optional[dict]:
    """POST to Supabase, return the inserted row dict. None on failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=_service_headers(), json=json_body)
        if resp.status_code in (200, 201):
            data = resp.json()
            return data[0] if isinstance(data, list) else data
        logger.warning(f"ALERT ENGINE: POST {url} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"ALERT ENGINE: POST {url} failed: {e}")
    return None


# -------------------------------------------------------------------------- #
# Alert Engine
# -------------------------------------------------------------------------- #

class AlertEngine:
    """
    Score-change detector. Safe to run concurrently — guards with _running flag.
    """

    def __init__(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def run_cycle(self) -> dict:
        """
        Execute one full alert cycle. Returns a summary dict::

            {
                "prospects_checked": int,
                "alerts_fired": int,
                "emails_sent": int,
                "errors": int,
                "timestamp": ISO-8601,
            }
        """
        if self._running:
            logger.warning("ALERT ENGINE: Cycle already in progress, skipping.")
            return {"skipped": True, "reason": "cycle_in_progress"}

        self._running = True
        try:
            logger.info("ALERT ENGINE: Starting cycle")
            summary = await self._execute_cycle()
            logger.info(
                f"ALERT ENGINE: Cycle complete — "
                f"checked={summary['prospects_checked']}, "
                f"alerts={summary['alerts_fired']}, "
                f"emails={summary['emails_sent']}, "
                f"errors={summary['errors']}"
            )
            return summary
        finally:
            self._running = False

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    async def _execute_cycle(self) -> dict:
        """Fetch all prospects and scan each for score changes."""
        base = settings.SUPABASE_URL

        # Fetch all prospects with their current intent scores in one query
        query = (
            "prospects"
            f"?select=id,user_id,full_name,company,email"
            f"&suppressed=eq.false"
            f"&intent_scores(score,tier,last_updated_at)"
            f"&limit=500"
        )
        prospects = await _supabase_get(f"{base}/rest/v1/{query}")

        alerts_fired = 0
        emails_sent = 0
        errors = 0

        for prospect in prospects:
            try:
                result = await self._check_prospect(base, prospect)
                if result.get("alert_fired"):
                    alerts_fired += 1
                if result.get("email_sent"):
                    emails_sent += 1
            except Exception as e:
                logger.error(f"ALERT ENGINE: Error checking prospect {prospect.get('id')}: {e}")
                errors += 1

        return {
            "prospects_checked": len(prospects),
            "alerts_fired": alerts_fired,
            "emails_sent": emails_sent,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _check_prospect(self, base: str, prospect: dict) -> dict:
        """
        Evaluate score changes for a single prospect.

        Baseline strategy:
          - Get the most recent alert record for this prospect (if any).
          - Its new_score becomes our comparison baseline.
          - If no prior alert exists, skip spike detection (no baseline).

        Returns: {"alert_fired": bool, "email_sent": bool}
        """
        prospect_id = prospect["id"]
        user_id = prospect["user_id"]
        email = prospect.get("email")
        name = prospect.get("full_name") or prospect.get("company") or "Unknown"
        company = prospect.get("company") or ""

        # ---- Current score from intent_scores ---------------------------------
        score_rows = await _supabase_get(
            f"{base}/rest/v1/intent_scores"
            f"?prospect_id=eq.{prospect_id}"
            f"&select=score,tier"
            f"&limit=1"
        )
        if not score_rows:
            return {"alert_fired": False, "email_sent": False}

        current = score_rows[0]
        current_score = float(current.get("score") or 0)
        current_tier = current.get("tier") or "cold"

        # ---- Last alert for this prospect (baseline score) --------------------
        last_alert_rows = await _supabase_get(
            f"{base}/rest/v1/alerts"
            f"?prospect_id=eq.{prospect_id}"
            f"&type=eq.score_spike"
            f"&order=created_at.desc"
            f"&select=new_score,created_at"
            f"&limit=1"
        )

        baseline_score: Optional[float] = None
        if last_alert_rows:
            baseline_score = float(last_alert_rows[0].get("new_score") or 0)

        alert_fired = False
        email_sent = False

        # ---- Check 1: Score spike (+20 pts) ---------------------------------
        if baseline_score is not None:
            change = current_score - baseline_score
            if change >= 20:
                await self._fire_alert(
                    base=base,
                    user_id=user_id,
                    prospect_id=prospect_id,
                    prospect_name=name,
                    company=company,
                    alert_type="score_spike",
                    title=f"{name} surged +{change:.0f}pts",
                    message=(
                        f"{name} at {company} spiked +{change:.0f} points "
                        f"(now {current_score:.0f}). Check now."
                    ),
                    payload={
                        "old_score": baseline_score,
                        "new_score": current_score,
                        "change": change,
                        "reason": f"+{change:.0f} points since last alert",
                    },
                )
                alert_fired = True
                if email:
                    email_sent = await send_score_alert_email(
                        to_email=email,
                        prospect_name=name,
                        company=company,
                        alert_type="score_spike",
                        old_score=baseline_score,
                        new_score=current_score,
                        prospect_id=prospect_id,
                    )

        # ---- Check 2: Tier change (any tier transition) -------------------------
        new_tier = _tier_from_score(current_score)
        if new_tier != current_tier:
            direction = "UP" if _tier_rank(new_tier) > _tier_rank(current_tier) else "DOWN"
            alert_type = "tier_up" if direction == "UP" else "tier_down"

            await self._fire_alert(
                base=base,
                user_id=user_id,
                prospect_id=prospect_id,
                prospect_name=name,
                company=company,
                alert_type=alert_type,
                title=f"{name} moved {direction} → {new_tier.upper()}",
                message=(
                    f"{name} at {company} moved from {current_tier.upper()} to "
                    f"{new_tier.upper()} tier (score: {current_score:.0f}). Time to engage."
                ),
                payload={
                    "old_tier": current_tier,
                    "new_tier": new_tier,
                    "old_score": current_score,
                    "new_score": current_score,
                    "direction": direction,
                },
            )
            alert_fired = True
            if email:
                tier_email_sent = await send_score_alert_email(
                    to_email=email,
                    prospect_name=name,
                    company=company,
                    alert_type=alert_type,
                    old_score=current_score,
                    new_score=current_score,
                    old_tier=current_tier,
                    new_tier=new_tier,
                    prospect_id=prospect_id,
                )
                if tier_email_sent:
                    email_sent = True

        return {"alert_fired": alert_fired, "email_sent": email_sent}

    async def _fire_alert(
        self,
        base: str,
        user_id: str,
        prospect_id: str,
        prospect_name: str,
        company: str,
        alert_type: str,
        title: str,
        message: str,
        payload: dict,
    ) -> None:
        """Insert one alert record into the alerts table."""
        row = {
            "user_id": user_id,
            "prospect_id": prospect_id,
            "name": f"Score Alert: {prospect_name}",
            "description": message,
            "alert_condition_type": "change",
            "condition_config": payload,
            "type": alert_type,
            "title": title,
            "message": message,
            "payload": payload,
            "is_active": True,
            "read": False,
        }
        result = await _supabase_post(f"{base}/rest/v1/alerts", row)
        if result:
            logger.info(f"ALERT ENGINE: Fired [{alert_type}] for {prospect_name}")
        else:
            logger.error(f"ALERT ENGINE: Failed to store [{alert_type}] for {prospect_name}")


def _tier_rank(tier: str) -> int:
    """Numeric rank for tier ordering (cold=0, warm=1, hot=2)."""
    return {"cold": 0, "warm": 1, "hot": 2}.get(tier, 0)
