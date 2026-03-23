"""
Prospects API - CRUD + Enrichment
Handles prospect management and triggers enrichment pipeline.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from typing import List, Optional
import httpx
import logging

from ..deps import get_current_user
from ...schemas.prospect import (
    ProspectCreate, ProspectUpdate, ProspectResponse,
    LinkedInScrapeRequest,
    FundingSignalCreate, FundingSignalResponse,
    ReviewScrapeRequest,
    TechnographicEnrichRequest,
    IntentScoreResponse,
    DraftEmailResponse, DraftEmailApprove,
)
from ...services.linkedin_scraper import scrape_prospect_linkedin, LinkedInScraper
from ...services.pain_point_extractor import extract_pain_points_from_posts, generate_personalized_email
from ...services.funding_signals import detect_funding_round, check_linkedin_jobs, detect_hiring_surge
from ...services.technographics import detect_technographics, check_technographic_gap
from ...services.review_scraper import scrape_all_competitors
from ...services.intent_scoring import calculate_intent_score, get_score_description, should_trigger_alert
from ...core.config import settings

router = APIRouter(prefix="/prospects", tags=["Prospects"])
logger = logging.getLogger(__name__)


def _get_headers(token: str = None) -> dict:
    """Get Supabase headers."""
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def _fetch_prospect(supabase_url: str, headers: dict, prospect_id: str) -> dict:
    """Fetch a single prospect by ID."""
    url = f"{supabase_url}/rest/v1/prospects?id=eq.{prospect_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data[0] if data else None
    return None


async def _fetch_signal(supabase_url: str, headers: dict, table: str, prospect_id: str) -> dict:
    """Fetch a single signal row for a prospect."""
    url = f"{supabase_url}/rest/v1/{table}?prospect_id=eq.{prospect_id}&limit=1"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data[0] if data else None
    return None


# =============================================
# CRUD ENDPOINTS
# =============================================

@router.get("", response_model=List[ProspectResponse])
async def list_prospects(
    skip: int = 0,
    limit: int = 50,
    tier: Optional[str] = None,  # hot, warm, cold
    current_user: dict = Depends(get_current_user),
):
    """List all prospects for the current user with optional tier filter."""
    user_id = current_user["id"]
    headers = _get_headers()

    params = [
        f"user_id=eq.{user_id}",
        "order=created_at.desc",
        f"limit={limit}",
        f"offset={skip}",
        "suppressed=eq.false",
    ]

    url = f"{settings.SUPABASE_URL}/rest/v1/prospects?" + "&".join(params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch prospects")

        prospects = response.json()

        # If tier filter requested, join with intent_scores
        if tier:
            scored = []
            for p in prospects:
                score_data = await _fetch_signal(
                    settings.SUPABASE_URL, headers, "intent_scores", p["id"]
                )
                if score_data and score_data.get("tier") == tier:
                    scored.append(p)
            return scored

        return prospects


@router.post("", response_model=ProspectResponse, status_code=201)
async def create_prospect(
    prospect: ProspectCreate,
    current_user: dict = Depends(get_current_user),
):
    """Add a new prospect."""
    user_id = current_user["id"]
    headers = _get_headers()

    data = {
        **prospect.model_dump(),
        "user_id": user_id,
    }

    url = f"{settings.SUPABASE_URL}/rest/v1/prospects"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=data, headers=headers)
        if response.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"Failed to create prospect: {response.text}")

        created = response.json()
        return created[0] if isinstance(created, list) else created


@router.get("/{prospect_id}", response_model=ProspectResponse)
async def get_prospect(
    prospect_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get a single prospect with all signals."""
    headers = _get_headers()
    headers["Prefer"] = "return=representation"

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Fetch related signals
    signals = {}
    for table in ["intent_scores", "linkedin_posts", "pain_points", "funding_signals",
                   "technographics", "review_signals", "draft_emails"]:
        data = await _fetch_signal(settings.SUPABASE_URL, headers, table, prospect_id)
        if data:
            signals[table] = data

    return {**prospect, "signals": signals}


@router.patch("/{prospect_id}", response_model=ProspectResponse)
async def update_prospect(
    prospect_id: str,
    update: ProspectUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update a prospect."""
    headers = _get_headers()

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    url = f"{settings.SUPABASE_URL}/rest/v1/prospects?id=eq.{prospect_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(url, json=update.model_dump(exclude_none=True), headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to update prospect")

        updated = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
        return updated


@router.delete("/{prospect_id}", status_code=204)
async def delete_prospect(
    prospect_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete (suppress) a prospect."""
    headers = _get_headers()

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    url = f"{settings.SUPABASE_URL}/rest/v1/prospects?id=eq.{prospect_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            url,
            json={"suppressed": True},
            headers=headers
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to delete prospect")


# =============================================
# ENRICHMENT ENDPOINTS
# =============================================

@router.post("/{prospect_id}/scrape")
async def scrape_prospect(
    prospect_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Scrape all available signals for a prospect.
    Runs: LinkedIn/careers + hiring + funding detection.
    Stores results and updates intent score.
    """
    headers = _get_headers()
    headers["Prefer"] = "return=representation"

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Run scraping pipeline
    result = scrape_prospect_linkedin(prospect)

    # Store careers data
    if result.get("careers"):
        careers = result["careers"]
        is_hiring = careers.get("hiring_active", False)
        job_count = careers.get("open_roles", 0)

        if is_hiring or job_count > 0:
            # Update funding_signals with hiring data
            hiring_signal = detect_hiring_surge(
                prospect.get("company", ""),
                job_count=job_count,
                careers_data=careers
            )

            if hiring_signal:
                url = f"{settings.SUPABASE_URL}/rest/v1/funding_signals"
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(url, json={
                        "prospect_id": prospect_id,
                        "company_name": prospect.get("company", ""),
                        "funding_amount": f"{job_count} roles",
                        "funding_stage": "hiring",
                        "announced_date": None,
                        "intent_score_boost": hiring_signal.get("intent_score_boost", 0),
                    }, headers=headers)

    # Store jobs data
    if result.get("jobs"):
        jobs = result["jobs"]
        if jobs.get("job_count", 0) > 0:
            url = f"{settings.SUPABASE_URL}/rest/v1/funding_signals"
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(url, json={
                    "prospect_id": prospect_id,
                    "company_name": prospect.get("company", ""),
                    "funding_amount": f"{jobs['job_count']} jobs",
                    "funding_stage": "hiring",
                    "announced_date": None,
                    "intent_score_boost": 10,
                }, headers=headers)

    # Update last_enriched_at
    update_url = f"{settings.SUPABASE_URL}/rest/v1/prospects?id=eq.{prospect_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.patch(update_url, json={"last_enriched_at": "now()"}, headers=headers)

    # Recalculate intent score
    await _recalculate_score(prospect_id, headers)

    return {
        "prospect_id": prospect_id,
        "scraped": True,
        "careers": result.get("careers", {}),
        "jobs": result.get("jobs", {}),
    }


@router.post("/{prospect_id}/enrich-technographics")
async def enrich_technographics(
    prospect_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Detect company tech stack from their website.
    Based on Playbook Module 06.
    """
    headers = _get_headers()

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    company_domain = prospect.get("company_domain", "")
    if not company_domain:
        raise HTTPException(status_code=400, detail="No company domain available for this prospect")

    # Detect technographics
    tech_data = detect_technographics(company_domain)

    # Store each detected tool
    for tool in tech_data.get("tools", []):
        url = f"{settings.SUPABASE_URL}/rest/v1/technographics"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(url, json={
                "prospect_id": prospect_id,
                "company_domain": company_domain,
                "tool_name": tool["tool_name"],
                "tool_category": tool["tool_category"],
                "is_competitor_tool": tool.get("is_competitor_tool", False),
            }, headers=headers)

    # Calculate gap analysis
    gap = check_technographic_gap(tech_data.get("tools", []))

    # Update intent score with technographic signal
    await _recalculate_score(prospect_id, headers, technographic_signal=gap.get("fit") == "high")

    return {
        "prospect_id": prospect_id,
        "technographics": tech_data,
        "gap_analysis": gap,
    }


@router.post("/{prospect_id}/scrape-reviews")
async def scrape_reviews(
    prospect_id: str,
    competitor_names: List[str],
    current_user: dict = Depends(get_current_user),
):
    """
    Scrape G2 and Capterra for competitor review signals.
    Based on Playbook Module 12.
    """
    headers = _get_headers()

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Scrape reviews for all competitor names
    reviews = scrape_all_competitors(competitor_names)

    stored = 0
    for review in reviews:
        url = f"{settings.SUPABASE_URL}/rest/v1/review_signals"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json={
                "prospect_id": prospect_id,
                "competitor_name": review.get("competitor_name", ""),
                "review_platform": review.get("review_platform", "G2"),
                "rating": review.get("rating", 0),
                "review_text": review.get("review_text", ""),
                "switching_intent": review.get("switching_intent", False),
                "pain_mentioned": review.get("pain_mentioned", ""),
            }, headers=headers)
            if r.status_code in [200, 201]:
                stored += 1

    # Update score
    has_switching = any(r.get("switching_intent", False) for r in reviews)
    await _recalculate_score(prospect_id, headers, review_signal=has_switching)

    return {
        "prospect_id": prospect_id,
        "reviews_found": len(reviews),
        "reviews_stored": stored,
        "reviews": reviews[:5],  # Return first 5 for review
    }


@router.post("/{prospect_id}/generate-draft")
async def generate_email_draft(
    prospect_id: str,
    signal_context: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate a personalized email draft using AI.
    Based on Playbook Module 17.
    """
    headers = _get_headers()

    prospect = await _fetch_prospect(settings.SUPABASE_URL, headers, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if prospect.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Generate email
    email = generate_personalized_email(
        prospect_name=prospect.get("first_name") or prospect.get("full_name", "there"),
        company=prospect.get("company", ""),
        signal_context=signal_context,
    )

    # Get the latest intent score
    intent = await _fetch_signal(settings.SUPABASE_URL, headers, "intent_scores", prospect_id)
    trigger_signal_id = intent.get("id") if intent else None

    # Store draft
    url = f"{settings.SUPABASE_URL}/rest/v1/draft_emails"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json={
            "prospect_id": prospect_id,
            "trigger_signal_type": intent.get("funding_signal") and "funding" or "general",
            "trigger_signal_id": trigger_signal_id,
            "subject_line": email.get("subject_line", ""),
            "first_line": email.get("first_line", ""),
            "full_email_body": email.get("email_body", ""),
            "signal_context": signal_context,
            "approved": False,
            "sent": False,
        }, headers=headers)

        if response.status_code in [200, 201]:
            draft = response.json()
            return {**email, "draft_id": draft[0].get("id") if isinstance(draft, list) else draft.get("id")}

    return email


@router.get("/{prospect_id}/hot-prospects")
async def get_hot_prospects(
    tier: str = "hot",
    current_user: dict = Depends(get_current_user),
):
    """
    Get prospects by tier (hot/warm/cold).
    Returns prospects with their signals and scores.
    """
    user_id = current_user["id"]
    headers = _get_headers()

    # Get all prospects for user
    url = f"{settings.SUPABASE_URL}/rest/v1/prospects?user_id=eq.{user_id}&suppressed=eq.false&select=*"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch prospects")

        all_prospects = response.json()

    hot_prospects = []
    for p in all_prospects:
        score_data = await _fetch_signal(settings.SUPABASE_URL, headers, "intent_scores", p["id"])
        if score_data and score_data.get("tier") == tier:
            # Fetch pain points for context
            pain_url = f"{settings.SUPABASE_URL}/rest/v1/pain_points?prospect_id=eq.{p['id']}&order=extracted_at.desc&limit=5"
            async with httpx.AsyncClient(timeout=30.0) as client:
                pain_resp = await client.get(pain_url, headers=headers)
                pain_points = pain_resp.json() if pain_resp.status_code == 200 else []

            # Get latest draft
            draft_url = f"{settings.SUPABASE_URL}/rest/v1/draft_emails?prospect_id=eq.{p['id']}&approved=eq.false&sent=eq.false&order=generated_at.desc&limit=1"
            async with httpx.AsyncClient(timeout=30.0) as client:
                draft_resp = await client.get(draft_url, headers=headers)
                drafts = draft_resp.json() if draft_resp.status_code == 200 else []

            hot_prospects.append({
                **p,
                "intent_score": score_data,
                "pain_points": pain_points,
                "draft_email": drafts[0] if drafts else None,
                "score_description": get_score_description(
                    score_data.get("score", 0),
                    score_data.get("score_breakdown", {})
                ),
            })

    # Sort by score descending
    hot_prospects.sort(key=lambda x: x["intent_score"].get("score", 0), reverse=True)
    return hot_prospects


# =============================================
# INTERNAL HELPERS
# =============================================

async def _recalculate_score(
    prospect_id: str,
    headers: dict,
    funding_signal: bool = False,
    hiring_signal: bool = False,
    review_signal: bool = False,
    technographic_signal: bool = False,
    linkedin_signal: bool = False,
):
    """Recalculate and update intent score for a prospect."""
    # Fetch all signal data
    funding_url = f"{settings.SUPABASE_URL}/rest/v1/funding_signals?prospect_id=eq.{prospect_id}&limit=1"
    pain_url = f"{settings.SUPABASE_URL}/rest/v1/pain_points?prospect_id=eq.{prospect_id}&order=extracted_at.desc&limit=10"
    review_url = f"{settings.SUPABASE_URL}/rest/v1/review_signals?prospect_id=eq.{prospect_id}&limit=1"
    tech_url = f"{settings.SUPABASE_URL}/rest/v1/technographics?prospect_id=eq.{prospect_id}&limit=20"

    async with httpx.AsyncClient(timeout=30.0) as client:
        funding_resp = await client.get(funding_url, headers=headers)
        pain_resp = await client.get(pain_url, headers=headers)
        review_resp = await client.get(review_url, headers=headers)
        tech_resp = await client.get(tech_url, headers=headers)

    funding_data = funding_resp.json() if funding_resp.status_code == 200 else []
    pain_data = pain_resp.json() if pain_resp.status_code == 200 else []
    review_data = review_resp.json() if review_resp.status_code == 200 else []
    tech_data = tech_resp.json() if tech_resp.status_code == 200 else []

    # Determine signals from data
    has_funding = any(f.get("funding_stage") != "hiring" for f in funding_data)
    has_hiring = any(f.get("funding_stage") == "hiring" for f in funding_data)
    has_review = len(review_data) > 0
    has_switching = any(r.get("switching_intent", False) for r in review_data)
    has_pain = any(p.get("sentiment") in ["negative", "frustrated"] for p in pain_data)
    has_frustrated = any(p.get("sentiment") == "frustrated" for p in pain_data)
    has_tech_gap = technographic_signal

    # Get existing score
    existing = await _fetch_signal(settings.SUPABASE_URL, headers, "intent_scores", prospect_id)
    existing_score = existing.get("score", 0.0) if existing else 0.0

    # Calculate new score
    score_result = calculate_intent_score(
        funding_signal=has_funding or funding_signal,
        hiring_signal=has_hiring or hiring_signal,
        review_signal=has_review or review_signal,
        review_switching_intent=has_switching,
        linkedin_pain=has_pain,
        linkedin_frustrated=has_frustrated,
        technographic_signal=has_tech_gap,
        existing_score=existing_score,
        score_breakdown=existing.get("score_breakdown", {}) if existing else None,
    )

    # Upsert intent_scores
    score_data = {
        "prospect_id": prospect_id,
        "score": score_result["score"],
        "tier": score_result["tier"],
        "funding_signal": score_result["funding_signal"],
        "hiring_signal": score_result["hiring_signal"],
        "review_signal": score_result["review_signal"],
        "linkedin_signal": score_result["linkedin_signal"],
        "technographic_signal": score_result["technographic_signal"],
        "website_visit_signal": score_result["website_visit_signal"],
        "score_breakdown": score_result["score_breakdown"],
        "last_updated_at": "now()",
    }

    if existing:
        url = f"{settings.SUPABASE_URL}/rest/v1/intent_scores?prospect_id=eq.{prospect_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.patch(url, json=score_data, headers=headers)
    else:
        url = f"{settings.SUPABASE_URL}/rest/v1/intent_scores"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(url, json=score_data, headers=headers)

    return score_result
