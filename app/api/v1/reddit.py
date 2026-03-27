"""
Reddit API — Reddit ad signals and organic mention signals.
Searches Reddit via PSAW (Pushshift) + Reddit API for ad and organic signals.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
import logging
from typing import Optional, List

from ..deps import get_current_user, get_supabase_service_client
from ...schemas.reddit import (
    RedditSearchRequest,
    RedditRefreshRequest,
    RedditAdSignalsResponse,
    RedditOrganicSignalsResponse,
)
from ...core.config import settings

router = APIRouter(prefix="/api/v1/reddit", tags=["Reddit"])
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


def _build_url(path: str, params: Optional[List[str]] = None) -> str:
    """Build a Supabase REST URL with optional query params."""
    url = f"{settings.SUPABASE_URL}/rest/v1{path}"
    if params:
        from urllib.parse import urlencode
        pairs = []
        for p in params:
            if '=' in p:
                key, val = p.split('=', 1)
                pairs.append((key, val))
        url = f"{url}?{urlencode(pairs)}"
    return url


async def _fetch_reddit_ad_signals(
    company_domain: str,
    user_id: str,
) -> Optional[dict]:
    """Fetch reddit_ad_signals row for a company + user."""
    headers = _svc_headers()

    params = [
        f"company_domain=eq.{company_domain}",
        f"user_id=eq.{user_id}",
        "limit=1",
    ]
    url = _build_url("/reddit_ad_signals", params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        return None

    records = resp.json()
    return records[0] if records else None


async def _fetch_reddit_organic_signals(
    company_domain: str,
    user_id: str,
) -> Optional[dict]:
    """Fetch reddit_organic_signals row for a company + user."""
    headers = _svc_headers()

    params = [
        f"company_domain=eq.{company_domain}",
        f"user_id=eq.{user_id}",
        "limit=1",
    ]
    url = _build_url("/reddit_organic_signals", params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        return None

    records = resp.json()
    return records[0] if records else None


# =============================================
# ENDPOINTS
# =============================================

@router.get("/search")
async def search_reddit(
    company_name: str = Query(..., description="Company name to search on Reddit"),
    company_domain: Optional[str] = Query(None, description="Company domain (optional)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Search Reddit for both ad and organic signals for a company.
    Calls RedditAdsService and RedditOrganicService and returns combined results.
    """
    if not company_name:
        raise HTTPException(status_code=400, detail="company_name is required")

    try:
        from ...services.reddit_ads_service import RedditAdsService
        from ...services.reddit_organic_service import RedditOrganicService

        svc_ads = RedditAdsService()
        svc_organic = RedditOrganicService()

        ads_result = await svc_ads.search_reddit_ads(
            company_name=company_name,
            company_domain=company_domain,
            user_id=current_user["id"],
        )
        organic_result = await svc_organic.search_reddit_organic(
            company_name=company_name,
            company_domain=company_domain,
            user_id=current_user["id"],
        )

        return {
            "searched_company": company_name,
            "searched_domain": company_domain,
            "ad_signals": ads_result,
            "organic_signals": organic_result,
        }
    except ImportError as e:
        logger.warning(f"Reddit service not yet implemented: {e}")
        raise HTTPException(
            status_code=503,
            detail="Reddit service not yet available. Please implement reddit_ads_service and reddit_organic_service.",
        )
    except Exception as e:
        logger.error(
            f"Reddit search failed for company_name={company_name}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Reddit search failed: {str(e)}",
        )


@router.get("/ads/{company_domain}", response_model=RedditAdSignalsResponse)
async def get_reddit_ad_signals(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cached Reddit ad signals for a company domain.
    Returns aggregated signals from reddit_ad_signals table including
    ad count, promoted posts found, and advertiser status.

    Returns 404 if no signals exist for this company.
    """
    user_id = current_user["id"]

    try:
        record = await _fetch_reddit_ad_signals(company_domain, user_id)
    except Exception as e:
        logger.error(
            f"Failed to fetch reddit_ad_signals for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching Reddit ad signals: {str(e)}",
        )

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No Reddit ad signals found for domain '{company_domain}'",
        )

    return record


@router.get("/organic/{company_domain}", response_model=RedditOrganicSignalsResponse)
async def get_reddit_organic_signals(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cached Reddit organic signals for a company domain.
    Returns aggregated signals from reddit_organic_signals table including
    mention count, sentiment, upvotes, and activity flags.

    Returns 404 if no signals exist for this company.
    """
    user_id = current_user["id"]

    try:
        record = await _fetch_reddit_organic_signals(company_domain, user_id)
    except Exception as e:
        logger.error(
            f"Failed to fetch reddit_organic_signals for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching Reddit organic signals: {str(e)}",
        )

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No Reddit organic signals found for domain '{company_domain}'",
        )

    return record


@router.post("/refresh")
async def refresh_reddit_signals(
    request: RedditRefreshRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Force-refresh Reddit ad and organic signals for a company.
    Re-fetches from Reddit API, recalculates signal scores,
    and upserts results into reddit_ad_signals + reddit_organic_signals tables.

    Use this when signals appear stale (>24h old).
    """
    try:
        from ...services.reddit_ads_service import RedditAdsService
        from ...services.reddit_organic_service import RedditOrganicService

        svc_ads = RedditAdsService()
        svc_organic = RedditOrganicService()

        ads_result = await svc_ads.refresh_signals(
            company_domain=request.company_domain,
            company_name=request.company_name,
            user_id=current_user["id"],
        )
        organic_result = await svc_organic.refresh_signals(
            company_domain=request.company_domain,
            company_name=request.company_name,
            user_id=current_user["id"],
        )

        return {
            "company_domain": request.company_domain,
            "company_name": request.company_name,
            "refreshed": True,
            "ad_signals": ads_result,
            "organic_signals": organic_result,
        }
    except ImportError as e:
        logger.warning(f"Reddit service not yet implemented: {e}")
        raise HTTPException(
            status_code=503,
            detail="Reddit service not yet available. Please implement reddit_ads_service and reddit_organic_service.",
        )
    except Exception as e:
        logger.error(
            f"Reddit refresh failed for {request.company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Reddit refresh failed: {str(e)}",
        )
