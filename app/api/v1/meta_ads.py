"""
Meta Ads API — Facebook/Meta Ads Library signals.
Searches Meta's Ads Library API and caches signals per company.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
import logging
from typing import Optional, List

from ..deps import get_current_user, get_supabase_service_client
from ...schemas.meta_ads import (
    MetaAdSearchRequest,
    MetaAdRefreshRequest,
    MetaAdSignalsResponse,
)
from ...services.meta_ads_service import MetaAdsService
from ...core.config import settings

router = APIRouter(prefix="/api/v1/meta-ads", tags=["Meta Ads"])
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


async def _fetch_signals(company_domain: str, user_id: str) -> Optional[dict]:
    """Fetch meta_ad_signals row for a company + user, with ads joined."""
    headers = _svc_headers()

    # Fetch signals record
    sig_params = [
        f"company_domain=eq.{company_domain}",
        f"user_id=eq.{user_id}",
        "limit=1",
    ]
    sig_url = _build_url("/meta_ad_signals", sig_params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        sig_resp = await client.get(sig_url, headers=headers)

    if sig_resp.status_code != 200:
        return None

    signals = sig_resp.json()
    if not signals:
        return None

    record = signals[0]

    # Fetch individual ads for this company
    ads_params = [
        f"company_domain=eq.{company_domain}",
        "order=ad_delivery_start.desc",
    ]
    ads_url = _build_url("/meta_ads", ads_params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        ads_resp = await client.get(ads_url, headers=headers)

    if ads_resp.status_code == 200:
        record["ads"] = ads_resp.json()
    else:
        record["ads"] = []

    return record


# =============================================
# ENDPOINTS
# =============================================

@router.get("/search")
async def search_ads(
    company_name: str = Query(..., description="Company name to search in Meta Ads Library"),
    company_domain: Optional[str] = Query(None, description="Company domain (optional)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Search Meta Ads Library for a company's active advertisements.
    Queries the Meta Graph API /ads_archive endpoint.

    Returns raw ad data including ad IDs, creative, delivery dates,
    and campaign objectives to determine ad spend intensity and strategy.
    """
    if not company_name:
        raise HTTPException(status_code=400, detail="company_name is required")

    try:
        svc = MetaAdsService()
        result = await svc.search_ads_library(
            company_name=company_name,
            company_domain=company_domain,
            user_id=current_user["id"],
        )
        return result
    except Exception as e:
        logger.error(
            f"Meta Ads search failed for company_name={company_name}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Meta Ads Library search failed: {str(e)}",
        )


@router.get("/signals/{company_domain}", response_model=MetaAdSignalsResponse)
async def get_signals(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cached Meta ad signals for a company domain.
    Returns aggregated signals from meta_ad_signals table including
    ad count, lead-gen flags, intensity score, and recency.

    Returns 404 if no signals exist for this company.
    """
    user_id = current_user["id"]

    try:
        record = await _fetch_signals(company_domain, user_id)
    except Exception as e:
        logger.error(
            f"Failed to fetch meta_ad_signals for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching signals: {str(e)}",
        )

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No Meta ad signals found for domain '{company_domain}'",
        )

    return record


@router.post("/refresh")
async def refresh_signals(
    request: MetaAdRefreshRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Force-refresh Meta ad signals for a company from the Ads Library API.
    Re-fetches all active ads, recalculates intensity/recency scores,
    and upserts the results into meta_ad_signals + meta_ads tables.

    Use this when signals appear stale (>24h old).
    """
    try:
        svc = MetaAdsService()
        result = await svc.refresh_signals(
            company_domain=request.company_domain,
            company_name=request.company_name,
            user_id=current_user["id"],
        )
        return {
            "company_domain": request.company_domain,
            "company_name": request.company_name,
            "refreshed": True,
            "result": result,
        }
    except Exception as e:
        logger.error(
            f"Meta Ads refresh failed for {request.company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Meta Ads refresh failed: {str(e)}",
        )


@router.get("/ads/{company_domain}")
async def list_ads(
    company_domain: str,
    current_user: dict = Depends(get_current_user),
):
    """
    List all individual ads cached for a company domain.
    Returns raw ad records from meta_ads table ordered by
    delivery start date descending.
    """
    user_id = current_user["id"]
    headers = _svc_headers()

    params = [
        f"company_domain=eq.{company_domain}",
        "order=ad_delivery_start.desc",
    ]
    url = _build_url("/meta_ads", params)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            ads = response.json()
            if not ads:
                raise HTTPException(
                    status_code=404,
                    detail=f"No ads found for domain '{company_domain}'",
                )
            return {"company_domain": company_domain, "ads": ads, "count": len(ads)}

        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch ads: {response.text}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to list ads for {company_domain}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching ads: {str(e)}",
        )
