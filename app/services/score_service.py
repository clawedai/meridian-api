"""
Canonical intent score recalculation.
Deduplicated from app.api.v1.prospects and app.api.v1.linkedin.
"""
import asyncio
import httpx
import logging

from ..core.config import settings

logger = logging.getLogger(__name__)


def _base_headers() -> dict:
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def _get(url: str, headers: dict) -> list:
    """GET a URL, return JSON list. Returns [] on failure."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        return data if isinstance(data, list) else []
    return []


async def _patch_or_post(prospect_id: str, score_data: dict, method: str):
    """Upsert intent_scores row — PATCH if exists, POST if not."""
    hdrs = _base_headers()
    hdrs["Prefer"] = "return=representation"

    # Check existence
    check_url = f"{settings.SUPABASE_URL}/rest/v1/intent_scores?prospect_id=eq.{prospect_id}&select=id"
    async with httpx.AsyncClient(timeout=30.0) as client:
        existing = await client.get(check_url, headers=hdrs)

    if existing.status_code == 200 and existing.text.strip():
        patch_url = f"{settings.SUPABASE_URL}/rest/v1/intent_scores?prospect_id=eq.{prospect_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.patch(patch_url, json=score_data, headers=hdrs)
    else:
        post_url = f"{settings.SUPABASE_URL}/rest/v1/intent_scores"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(post_url, json={**score_data, "prospect_id": prospect_id}, headers=hdrs)


async def get_reddit_ad_signals(prospect_id: str, hdrs: dict) -> dict:
    """
    Fetch reddit_ad_signals record for a prospect from the DB.
    Returns a dict with ad_active and intensity fields.
    """
    data = await _get(
        f"{settings.SUPABASE_URL}/rest/v1/reddit_ad_signals?prospect_id=eq.{prospect_id}&limit=1",
        hdrs,
    )
    if not data:
        return {"ad_active": False, "intensity": 0}
    row = data[0]
    return {
        "ad_active": bool(row.get("is_advertiser", False)),
        "intensity": int(row.get("reddit_intensity", 0)),
    }


async def get_reddit_organic_signals(prospect_id: str, hdrs: dict) -> dict:
    """
    Fetch reddit_organic_signals record for a prospect from the DB.
    Returns a dict with organic_active, sentiment_label, and intensity fields.
    """
    data = await _get(
        f"{settings.SUPABASE_URL}/rest/v1/reddit_organic_signals?prospect_id=eq.{prospect_id}&limit=1",
        hdrs,
    )
    if not data:
        return {"organic_active": False, "sentiment_label": "neutral", "intensity": 0}
    row = data[0]
    return {
        "organic_active": bool(row.get("reddit_organic_active", False)),
        "sentiment_label": str(row.get("sentiment_label", "neutral")),
        "intensity": int(row.get("reddit_intensity", 0)),
    }


async def get_meta_ad_signals(prospect_id: str, hdrs: dict) -> dict:
    """
    Fetch meta_ad_signals record for a prospect from the DB.
    Returns a dict with active, intensity, is_lead_gen, and recency fields.
    """
    data = await _get(
        f"{settings.SUPABASE_URL}/rest/v1/meta_ad_signals?prospect_id=eq.{prospect_id}&limit=1",
        hdrs,
    )
    if not data:
        return {"active": False, "intensity": 0, "is_lead_gen": False, "recency": 0}
    row = data[0]
    return {
        "active": bool(row.get("is_active", False)),
        "intensity": int(row.get("ad_intensity", 0)),
        "is_lead_gen": bool(row.get("is_lead_gen", False)),
        "recency": int(row.get("recency_days", 0)),
    }


async def get_google_ads_signals(prospect_id: str, hdrs: dict) -> dict:
    """
    Fetch google_ads_signals record for a prospect from the DB.
    Returns a dict with is_active, intensity, and keyword_themes fields.
    """
    data = await _get(
        f"{settings.SUPABASE_URL}/rest/v1/google_ads_signals?prospect_id=eq.{prospect_id}&limit=1",
        hdrs,
    )
    if not data:
        return {"is_active": False, "intensity": 0, "keyword_themes": 0}
    row = data[0]
    return {
        "is_active": bool(row.get("is_advertiser", False)),
        "intensity": int(row.get("campaigns_found", 0)),
        "keyword_themes": int(row.get("high_intent_keywords", 0)),
    }


async def get_instagram_signals(prospect_id: str, hdrs: dict) -> dict:
    """
    Fetch instagram_signals record for a prospect from the DB.
    Returns a dict with is_active, engagement, posting_frequency, and follower_growth.
    """
    data = await _get(
        f"{settings.SUPABASE_URL}/rest/v1/instagram_signals?prospect_id=eq.{prospect_id}&limit=1",
        hdrs,
    )
    if not data:
        return {"is_active": False, "engagement": 0, "posting_frequency": 0, "follower_growth": 0}
    row = data[0]
    return {
        "is_active": bool(row.get("is_active", False)),
        "engagement": int(row.get("engagement_rate", 0)),
        "posting_frequency": int(row.get("posting_frequency", 0)),
        "follower_growth": int(row.get("follower_growth", 0)),
    }


async def recalculate_score(
    prospect_id: str,
    headers: dict = None,
    funding_signal: bool = False,
    hiring_signal: bool = False,
    review_signal: bool = False,
    technographic_signal: bool = False,
    linkedin_signal: bool = False,
    meta_ad_active: bool = False,
    meta_ad_intensity: int = 0,
    meta_ad_lead_gen: bool = False,
    meta_ad_recency: int = 0,
    reddit_ad_active: bool = False,
    reddit_organic_active: bool = False,
    reddit_sentiment_label: str = "neutral",
    reddit_intensity: int = 0,
    google_ad_active: bool = False,
    google_ad_intensity: int = 0,
    google_ad_keyword_themes: int = 0,
    instagram_active: bool = False,
    instagram_engagement: int = 0,
    instagram_posting_frequency: int = 0,
    instagram_follower_growth: int = 0,
) -> dict:
    """
    Fetch all signals for a prospect, recalculate intent score, upsert result.

    Returns the score_result dict from calculate_intent_score.
    """
    from .intent_scoring import calculate_intent_score

    hdrs = headers or _base_headers()

    # Parallel fetch of all signal tables
    (
        funding_data,
        pain_data,
        review_data,
        tech_data,
        meta_ad_data,
        reddit_ad_data,
        reddit_organic_data,
        google_ads_data,
        instagram_data,
        existing_data,
    ) = await asyncio.gather(
        _get(f"{settings.SUPABASE_URL}/rest/v1/funding_signals?prospect_id=eq.{prospect_id}&limit=1", hdrs),
        _get(f"{settings.SUPABASE_URL}/rest/v1/pain_points?prospect_id=eq.{prospect_id}&order=extracted_at.desc&limit=10", hdrs),
        _get(f"{settings.SUPABASE_URL}/rest/v1/review_signals?prospect_id=eq.{prospect_id}&limit=1", hdrs),
        _get(f"{settings.SUPABASE_URL}/rest/v1/technographics?prospect_id=eq.{prospect_id}&limit=20", hdrs),
        get_meta_ad_signals(prospect_id, hdrs),
        get_reddit_ad_signals(prospect_id, hdrs),
        get_reddit_organic_signals(prospect_id, hdrs),
        get_google_ads_signals(prospect_id, hdrs),
        get_instagram_signals(prospect_id, hdrs),
        _get(f"{settings.SUPABASE_URL}/rest/v1/intent_scores?prospect_id=eq.{prospect_id}&limit=1", hdrs),
    )

    # Determine signal presence
    has_funding = any(f.get("funding_stage") and f.get("funding_stage") != "hiring" for f in funding_data)
    has_hiring = any(f.get("funding_stage") == "hiring" for f in funding_data)
    has_review = len(review_data) > 0
    has_switching = any(r.get("switching_intent", False) for r in review_data)
    has_pain = any(p.get("sentiment") in ["negative", "frustrated"] for p in pain_data)
    has_frustrated = any(p.get("sentiment") == "frustrated" for p in pain_data)

    # Determine meta_ad signal presence (override with DB values if present)
    has_meta_ad = meta_ad_data.get("active", False)
    meta_ad_intensity_val = meta_ad_data.get("intensity", 0) if has_meta_ad else meta_ad_intensity
    meta_ad_lead_gen_val = meta_ad_data.get("is_lead_gen", False) if has_meta_ad else meta_ad_lead_gen
    meta_ad_recency_val = meta_ad_data.get("recency", 0) if has_meta_ad else meta_ad_recency

    # Determine Reddit signal presence (override with DB values if present)
    has_reddit_ad = reddit_ad_data.get("ad_active", False)
    has_reddit_organic = reddit_organic_data.get("organic_active", False)
    reddit_sentiment_val = reddit_organic_data.get("sentiment_label", "neutral") if has_reddit_organic else reddit_sentiment_label
    reddit_intensity_val = reddit_organic_data.get("intensity", 0) if has_reddit_organic else reddit_intensity

    # Determine Google Ads signal presence (override with DB values if present)
    has_google_ad = google_ads_data.get("is_active", False)
    google_ad_intensity_val = google_ads_data.get("intensity", 0) if has_google_ad else google_ad_intensity
    google_ad_keyword_themes_val = google_ads_data.get("keyword_themes", 0) if has_google_ad else google_ad_keyword_themes

    # Determine Instagram signal presence (override with DB values if present)
    has_instagram = instagram_data.get("is_active", False)
    instagram_engagement_val = instagram_data.get("engagement", 0) if has_instagram else instagram_engagement
    instagram_posting_freq_val = instagram_data.get("posting_frequency", 0) if has_instagram else instagram_posting_frequency
    instagram_follower_growth_val = instagram_data.get("follower_growth", 0) if has_instagram else instagram_follower_growth

    existing = existing_data[0] if existing_data else {}
    existing_score = existing.get("score", 0.0) if existing else 0.0

    # Calculate new score
    score_result = calculate_intent_score(
        funding_signal=has_funding or funding_signal,
        hiring_signal=has_hiring or hiring_signal,
        review_signal=has_review or review_signal,
        review_switching_intent=has_switching,
        linkedin_pain=has_pain,
        linkedin_frustrated=has_frustrated,
        technographic_signal=technographic_signal,
        meta_ad_active=has_meta_ad,
        meta_ad_intensity=meta_ad_intensity_val,
        meta_ad_lead_gen=meta_ad_lead_gen_val,
        meta_ad_recency=meta_ad_recency_val,
        reddit_ad_active=has_reddit_ad,
        reddit_organic_active=has_reddit_organic,
        reddit_sentiment_label=reddit_sentiment_val,
        reddit_intensity=reddit_intensity_val,
        google_ad_active=has_google_ad,
        google_ad_intensity=google_ad_intensity_val,
        google_ad_keyword_themes=google_ad_keyword_themes_val,
        instagram_active=has_instagram or instagram_active,
        instagram_engagement=instagram_engagement_val,
        instagram_posting_frequency=instagram_posting_freq_val,
        instagram_follower_growth=instagram_follower_growth_val,
        existing_score=existing_score,
        score_breakdown=existing.get("score_breakdown") if existing else None,
    )

    # Upsert intent_scores
    score_data = {
        "score": score_result["score"],
        "tier": score_result["tier"],
        "funding_signal": score_result["funding_signal"],
        "hiring_signal": score_result["hiring_signal"],
        "review_signal": score_result["review_signal"],
        "linkedin_signal": score_result["linkedin_signal"] or linkedin_signal,
        "technographic_signal": score_result["technographic_signal"],
        "website_visit_signal": score_result["website_visit_signal"],
        "meta_ad_signal": score_result["meta_ad_signal"],
        "reddit_signal": score_result["reddit_signal"],
        "google_ad_signal": score_result["google_ad_signal"],
        "instagram_signal": score_result["instagram_signal"],
        "score_breakdown": score_result["score_breakdown"],
        "last_updated_at": "now()",
    }
    await _patch_or_post(prospect_id, score_data, "upsert")

    logger.info(f"Recalculated score for {prospect_id}: {score_result['score']} ({score_result['tier']})")
    return score_result
