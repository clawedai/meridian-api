"""
LinkedIn Authentication + Scraping API.
User logs in once -> session stored -> used for all prospect scraping.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import httpx
import logging

from ..deps import get_current_user
from ...schemas.linkedin import (
    LinkedInLoginRequest, LinkedInLoginResponse,
    LinkedInScrapeRequest, LinkedInScrapeResponse,
    LinkedInStatusResponse,
)
from ...services.linkedin_session import (
    get_linkedin_manager,
    encrypt_cookies, decrypt_cookies,
)
from ...services.score_service import recalculate_score
from ...core.config import settings

router = APIRouter(prefix="/linkedin", tags=["LinkedIn"])
logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def _get_user_session(user_id: str) -> Optional[dict]:
    """Get the user's stored LinkedIn session from DB."""
    headers = _headers()
    headers["Prefer"] = "return=representation"
    url = f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?user_id=eq.{user_id}&is_valid=eq.true&select=*"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 200 and resp.text.strip():
        sessions = resp.json()
        if isinstance(sessions, list) and len(sessions) > 0:
            return sessions[0]
    return None


@router.post("/login", response_model=LinkedInLoginResponse)
async def linkedin_login(
    request: LinkedInLoginRequest,
    current_user: dict = Depends(get_current_user),
):
    """Log into LinkedIn with credentials. One login -> unlimited scraping."""
    user_id = current_user["id"]
    manager = get_linkedin_manager()
    result = await manager.login(request.email, request.password)

    if not result.get("success"):
        return LinkedInLoginResponse(success=False, error=result.get("error", "Login failed"))

    cookies = result["cookies"]
    username = result.get("username", request.email.split("@")[0])
    encrypted = encrypt_cookies(cookies)

    hdrs = _headers()
    # Delete existing sessions
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(
            f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?user_id=eq.{user_id}",
            headers={**hdrs, "Prefer": "return=representation"},
        )
        resp = await client.post(
            f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions",
            json={"user_id": user_id, "encrypted_cookies": encrypted, "username": username, "is_valid": True},
            headers={**hdrs, "Prefer": "return=representation"},
        )

    if resp.status_code not in (200, 201):
        logger.error(f"Failed to store LinkedIn session: {resp.status_code} -- {resp.text}")
        return LinkedInLoginResponse(success=False, error="Failed to store session. Try again.")

    logger.info(f"LinkedIn session stored for user {user_id} ({username})")
    return LinkedInLoginResponse(success=True, username=username)


@router.get("/status", response_model=LinkedInStatusResponse)
async def linkedin_status(current_user: dict = Depends(get_current_user)):
    """Check if user has a valid LinkedIn session."""
    session = await _get_user_session(current_user["id"])
    if not session:
        return LinkedInStatusResponse(logged_in=False)
    return LinkedInStatusResponse(
        logged_in=True,
        username=session.get("username"),
        last_used_at=session.get("last_used_at"),
        is_valid=session.get("is_valid", True),
    )


@router.post("/scrape", response_model=LinkedInScrapeResponse)
async def linkedin_scrape(
    request: LinkedInScrapeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Scrape a LinkedIn URL using the user's stored session."""
    user_id = current_user["id"]
    session = await _get_user_session(user_id)
    if not session:
        raise HTTPException(status_code=400, detail="Please log into LinkedIn first. Go to Settings.")

    try:
        cookies = decrypt_cookies(session["encrypted_cookies"])
    except Exception:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.patch(
                f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?id=eq.{session['id']}",
                json={"is_valid": False},
                headers=_headers(),
            )
        raise HTTPException(status_code=400, detail="LinkedIn session expired. Please log in again.")

    # Validate session
    manager = get_linkedin_manager()
    is_valid = await manager.validate_session(cookies)
    if not is_valid:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.patch(
                f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?id=eq.{session['id']}",
                json={"is_valid": False},
                headers=_headers(),
            )
        raise HTTPException(status_code=400, detail="LinkedIn session expired. Please log in again.")

    # Update last_used_at
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.patch(
            f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?id=eq.{session['id']}",
            json={"last_used_at": "now()"},
            headers=_headers(),
        )

    # Scrape
    result = await manager.scrape_url(request.url, cookies)
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)

    hdrs = _headers()
    hdrs["Prefer"] = "return=representation"
    posts_stored = 0
    score_delta = 0

    if result.posts:
        from ...services.pain_point_extractor import extract_pain_points_from_posts
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        for post in result.posts[:20]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.SUPABASE_URL}/rest/v1/linkedin_posts",
                    json={
                        "prospect_id": request.prospect_id,
                        "post_text": post.post_text[:2000],
                        "engagement_likes": post.likes,
                        "engagement_comments": post.comments,
                        "engagement_shares": post.shares,
                        "posted_at": post.posted_at,
                    },
                    headers=hdrs,
                )
                if resp.status_code in (200, 201):
                    posts_stored += 1
                    try:
                        # Extract pain points via AI
                        pain_points = extract_pain_points_from_posts([{"post_text": post.post_text}])
                        for pp in pain_points:
                            await client.post(
                                f"{settings.SUPABASE_URL}/rest/v1/pain_points",
                                json={
                                    "prospect_id": request.prospect_id,
                                    "pain_category": pp.get("pain_category", "process_pain"),
                                    "pain_description": pp.get("pain_description", ""),
                                    "tools_mentioned": pp.get("tools_mentioned", []),
                                    "goals_expressed": pp.get("goals_expressed", []),
                                    "sentiment": pp.get("sentiment", "neutral"),
                                    "confidence_score": 0.7,
                                    "extracted_at": now,
                                },
                                headers=hdrs,
                            )
                    except Exception as e:
                        logger.warning(f"Pain point extraction error: {e}")

    # Hiring signals
    if result.hiring_signals.get("hiring_active"):
        score_delta += 20
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{settings.SUPABASE_URL}/rest/v1/funding_signals",
                json={
                    "prospect_id": request.prospect_id,
                    "company_name": "",
                    "funding_amount": f"{result.hiring_signals.get('open_roles', 0)} roles",
                    "funding_stage": "hiring",
                    "intent_score_boost": 20,
                },
                headers=hdrs,
            )

    # Recalculate score
    await recalculate_score(request.prospect_id, hdrs, linkedin_signal=True)

    logger.info(f"LinkedIn scrape: prospect={request.prospect_id}, posts={posts_stored}, hiring={result.hiring_signals.get('hiring_active')}")
    return LinkedInScrapeResponse(
        success=True,
        prospect_id=request.prospect_id,
        posts_found=posts_stored,
        hiring_active=result.hiring_signals.get("hiring_active", False),
        open_roles=result.hiring_signals.get("open_roles", 0),
        score_delta=score_delta,
    )


@router.post("/logout")
async def linkedin_logout(current_user: dict = Depends(get_current_user)):
    """Delete the user's LinkedIn session."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(
            f"{settings.SUPABASE_URL}/rest/v1/linkedin_sessions?user_id=eq.{current_user['id']}",
            headers=_headers(),
        )
    return {"message": "LinkedIn session deleted"}

