"""
Google Ads API — Google Ads Transparency Report signals.
Queries Google's Ads Transparency API and caches signals per company.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
import logging
from typing import Optional, List

from ..deps import get_current_user, get_supabase_service_client
from ...schemas.google_ads import (
    GoogleAdsSearchRequest,
    GoogleAdsRefreshRequest,
    GoogleAdsSignalsResponse,
)
from ...services.google_ads_service import GoogleAdsService
from ...core.config import settings

router = APIRouter(prefix="/api/v1/google-ads", tags=["Google Ads"])
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
            if "=" in p:
                key, val = p.split("=", 1)
                pairs.append((key, val))
        url = f"{url}?{urlencode(pairs)}"
    return url


async def _fetch_signals(company_domain: str, user_id: str) -> Optional[dict]:
    """Fetch google_ads_signals row for a company + user, enriched with keyword themes."""
    headers = _svc_headers()

    # Fetch signals record from google_ads_signals
    sig_params = [
        f"company_domain=eq.{company_domain}",
        f"user_id=eq.{user_id}",
        "limit=1",
    ]
    sig_url = _build_url("/google_ads_signals", sig_params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        sig_resp = await client.get(sig_url, headers=headers)

    if sig_resp.status_code != 200:
        return None

    signals = sig_resp.json()
    if not signals:
        return None

    record = signals[0]

    # Fetch individual ads for this company (if google_ads table exists)
    ads_params = [
        f"company_domain=eq.{company_domain}",
        "order=ad_first_seen.desc",
    ]
    ads_url = _build_url("/google_ads", ads_params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        ads_resp = await client.get(ads_url, headers=headers)

    if ads_resp.status_code == 200:
        record["ads"] = ads_resp.json()
    else:
        record["ads"] = []

    # Enrich with keyword themes from raw response data
    raw = record.get("raw_response") or {}
    svc = GoogleAdsService()
    keyword_themes = svc.extract_keyword_themes(raw)
    record["keyword_themes_data"] = keyword_themes

    return record


# =============================================
# ENDPOINTS
# =============================================

@router.get("/search")
async def search_ads(
    company_name: str = Query(..., description="Company name to search in Google Ads Transparency Report"),
    company_domain: Optional[str] = Query(None, description="Company domain (optional)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Search Google Ads Transparency Report for a company's active advertisements.
    Queries Google's Ad Transparency API.

    Returns raw ad data including ad counts, domains advertised on,
    and advertiser info to determine ad spend intensity and keyword themes.
    """
    if not company_name:
        raise HTTPException(status_code=400, detail="company_name is required")

    try:
        svc = GoogleAdsService()
        result = await svc.search_ads(
            company_name=company_name,
            company_domain=company_domain,
        )
        signals = svc.build_signals(result)
        await svc.store_signals(
            user_id=current_user["id"],
            company_domain=company_domain or "",
            company_name=company_name,
            signals=signals,
            raw_data=result,
        )
        return {
            "searched_company": company_name,
            "searched_domain": company_domain,
            "signals": signals,
            "raw_data": result,
        }
    except Exception as e:
        logger.error(
            f"Google Ads search failed for company_name={company_name}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Google Ads Transparency search failed: {str(e)}",
        )


@router.get("/signals/{company_domain}", response_model=GoogleAdsSignalsResponse)
async def get_signals(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cached Google ad signals for a company domain.
    Returns aggregated signals from google_ads_signals table including
    ad count, intensity score, keyword themes score, and recency.

    Returns 404 if no signals exist for this company.
    """
    user_id = current_user["id"]

    try:
        record = await _fetch_signals(company_domain, user_id)
    except Exception as e:
        logger.error(
            f"Failed to fetch google_ads_signals for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching signals: {str(e)}",
        )

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No Google ad signals found for domain '{company_domain}'",
        )

    return record


@router.post("/refresh")
async def refresh_signals(
    request: GoogleAdsRefreshRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Force-refresh Google ad signals for a company from the Transparency API.
    Re-fetches all active ads, recalculates intensity/recency/keyword-theme scores,
    and upserts the results into google_ads_signals table.

    Use this when signals appear stale (>24h old).
    """
    try:
        svc = GoogleAdsService()
        result = await svc.refresh_signals(
            user_id=current_user["id"],
            company_domain=request.company_domain,
            company_name=request.company_name,
        )
        return {
            "company_domain": request.company_domain,
            "company_name": request.company_name,
            "refreshed": True,
            "result": result,
        }
    except Exception as e:
        logger.error(
            f"Google Ads refresh failed for {request.company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Google Ads refresh failed: {str(e)}",
        )


@router.get("/details/{company_domain}")
async def get_details(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get detailed Google ad data for a company domain.
    Returns raw signals record from google_ads_signals table,
    plus individual ad records from google_ads table if available.
    """
    user_id = current_user["id"]
    headers = _svc_headers()

    # Fetch signals record
    sig_params = [
        f"company_domain=eq.{company_domain}",
        f"user_id=eq.{user_id}",
        "limit=1",
    ]
    sig_url = _build_url("/google_ads_signals", sig_params)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            sig_resp = await client.get(sig_url, headers=headers)

        if sig_resp.status_code != 200 or not sig_resp.json():
            raise HTTPException(
                status_code=404,
                detail=f"No Google ad signals found for domain '{company_domain}'",
            )

        signals_record = sig_resp.json()[0]

        # Fetch individual ads (if table exists)
        ads_params = [
            f"company_domain=eq.{company_domain}",
            "order=ad_first_seen.desc",
        ]
        ads_url = _build_url("/google_ads", ads_params)
        async with httpx.AsyncClient(timeout=30.0) as client:
            ads_resp = await client.get(ads_url, headers=headers)

        ads = ads_resp.json() if ads_resp.status_code == 200 else []

        # Enrich with keyword theme analysis
        svc = GoogleAdsService()
        raw = signals_record.get("raw_response") or {}
        keyword_themes = svc.extract_keyword_themes(raw)
        signals_record["keyword_themes_data"] = keyword_themes

        return {
            "company_domain": company_domain,
            "signals": signals_record,
            "ads": ads,
            "ad_count": len(ads),
            "keyword_themes": keyword_themes,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get details for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching details: {str(e)}",
        )
