"""
Instagram Organic Intelligence API — Playwright-based profile scraping.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import logging

from ..deps import get_current_user
from ...schemas.instagram import (
    InstagramSignalsResponse,
    InstagramRefreshRequest,
)
from ...services.instagram_service import InstagramService
from ...core.config import settings

router = APIRouter(prefix="/api/v1/instagram", tags=["Instagram"])
logger = logging.getLogger(__name__)


# =============================================
# HELPERS
# =============================================

def _svc_headers() -> dict:
    """Return headers for service-role Supabase calls (bypasses RLS)."""
    return {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _build_url(path: str, params: list = None) -> str:
    """Build a Supabase REST URL with optional query params."""
    url = f"{settings.SUPABASE_URL}/rest/v1{path}"
    if params:
        from urllib.parse import urlencode
        pairs = []
        for p in params:
            if "=" in p:
                key, val = p.split("=", 1)
                pairs.append((key, val))
        url = f"{url}?{urlencode(pairs)}"
    return url


async def _fetch_signals(user_id: str, instagram_handle: str) -> dict | None:
    """Fetch instagram_signals row for a user + handle from Supabase."""
    import httpx

    handle = instagram_handle.lstrip("@")
    params = [
        f"user_id=eq.{user_id}",
        f"instagram_handle=eq.{handle}",
        "limit=1",
    ]
    url = _build_url("/instagram_signals", params)
    hdrs = _svc_headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=hdrs)
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
        return None
    except Exception as e:
        logger.error(f"INSTAGRAM: Failed to fetch signals: {e}")
        return None


# =============================================
# ENDPOINTS
# =============================================

@router.get("/signals/{instagram_handle}", response_model=InstagramSignalsResponse)
async def get_instagram_signals(
    instagram_handle: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cached Instagram signals for a handle.
    Returns aggregated signals from instagram_signals table including
    follower count, post count, engagement score, and hashtag themes.

    Returns 404 if no signals exist for this handle.
    """
    user_id = current_user["id"]

    try:
        record = await _fetch_signals(user_id, instagram_handle)
    except Exception as e:
        logger.error(
            f"INSTAGRAM: Failed to fetch cached signals for @{instagram_handle}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching signals: {str(e)}",
        )

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No Instagram signals found for handle '@{instagram_handle.lstrip('@')}'",
        )

    return record


@router.post("/refresh", response_model=InstagramSignalsResponse)
async def refresh_instagram_signals(
    body: InstagramRefreshRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Scrape fresh Instagram data for a handle from the public profile page.
    Uses Playwright to render the page and extract follower/post counts.
    Results are cached in instagram_signals table.

    Use this when signals appear stale or you want to scan a new handle.
    """
    user_id = current_user["id"]

    try:
        svc = InstagramService()
        result = await svc.refresh_signals(
            user_id=user_id,
            prospect_id=body.prospect_id,
            instagram_handle=body.instagram_handle,
        )

        if result.get("error"):
            raise HTTPException(
                status_code=502,
                detail=f"Instagram scrape failed: {result['error']}",
            )

        # Recalculate intent score after instagram signals are stored
        try:
            from ...services.score_service import recalculate_score
            await recalculate_score(
                prospect_id=body.prospect_id,
                instagram_active=result.get("signals", {}).get("is_active", False),
                instagram_engagement=result.get("signals", {}).get("engagement_rate", 0),
                instagram_posting_frequency=result.get("signals", {}).get("posting_frequency", 0),
                instagram_follower_growth=result.get("signals", {}).get("follower_growth", 0),
            )
        except Exception as e:
            logger.warning(f"INSTAGRAM: Failed to recalculate score: {e}")

        return {
            "instagram_handle": body.instagram_handle.lstrip("@"),
            "is_active": result.get("signals", {}).get("is_active", False),
            "followers": result.get("signals", {}).get("followers", 0),
            "following": result.get("signals", {}).get("following", 0),
            "posts": result.get("signals", {}).get("posts", 0),
            "instagram_intensity": result.get("signals", {}).get("instagram_intensity", 0),
            "instagram_active_score": result.get("signals", {}).get("instagram_active_score", 0),
            "engagement_rate": result.get("signals", {}).get("engagement_rate", 0),
            "posting_frequency": result.get("signals", {}).get("posting_frequency", 0),
            "follower_growth": result.get("signals", {}).get("follower_growth", 0),
            "hashtag_themes": result.get("signals", {}).get("hashtag_themes", []),
            "posts_analyzed": result.get("signals", {}).get("posts_analyzed", 0),
            "scraped_at": result.get("signals", {}).get("scraped_at"),
            "fetched_at": result.get("refreshed_at"),
            "url": result.get("signals", {}).get("url"),
            "refreshed": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"INSTAGRAM: Refresh failed for @{body.instagram_handle}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Instagram refresh failed: {str(e)}",
        )


@router.get("/preview/{instagram_handle}")
async def preview_instagram(
    instagram_handle: str,
    current_user: dict = Depends(get_current_user),
):
    """
    One-shot scrape of an Instagram profile without storing results.
    Useful for previewing signals before committing to a prospect.
    """
    try:
        svc = InstagramService()
        data = await svc.get_instagram_data(instagram_handle)
        return data
    except Exception as e:
        logger.error(
            f"INSTAGRAM: Preview failed for @{instagram_handle}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Instagram preview failed: {str(e)}",
        )
