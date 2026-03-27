"""
Module 05: Intent Scoring Engine
Combines all signals into a hot/warm/cold score per prospect.
Based on Playbook Step 13-14: scoring signals into tiers.
"""
from typing import Optional
from datetime import datetime
from app.schemas.prospect import (
    SCORE_WEIGHTS, TIER_HOT, TIER_WARM
)


def calculate_intent_score(
    funding_signal: bool = False,
    hiring_signal: bool = False,
    review_signal: bool = False,
    review_switching_intent: bool = False,
    linkedin_pain: bool = False,
    linkedin_frustrated: bool = False,
    technographic_signal: bool = False,
    website_visit: bool = False,
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
    instagram_engagement: int = 0,      # 0-10
    instagram_posting_frequency: int = 0,  # 0-10
    instagram_follower_growth: int = 0,  # 0-5
    existing_score: float = 0.0,
    score_breakdown: dict = None
) -> dict:
    """
    Calculate the composite intent score for a prospect.
    Based on the playbook's scoring methodology.

    Args:
        funding_signal: Company raised funding recently
        hiring_signal: Company is hiring, especially RevOps/Sales roles
        review_signal: Negative reviews found on competitor products
        review_switching_intent: Explicit switching intent in reviews
        linkedin_pain: LinkedIn posts mentioning pain points
        linkedin_frustrated: Posts with frustrated sentiment
        technographic_signal: Using CRM but no sales intel tool
        website_visit: Prospect visited the website/pricing page
        meta_ad_active: Active Meta ads in last 30 days
        meta_ad_intensity: 0-25 score (3+ ads OR >21 days running)
        meta_ad_lead_gen: Lead gen ad format present
        meta_ad_recency: 0-15 score (days since first seen)
        reddit_ad_active: Company advertising on Reddit
        reddit_organic_active: Active Reddit mentions (5+ posts)
        reddit_sentiment_label: "positive", "negative", or "neutral"
        reddit_intensity: 0-20 score (10+ mentions OR 50+ upvotes)
        google_ad_active: Company is advertising on Google
        google_ad_intensity: 0-15 score (3+ campaigns OR high ad volume)
        google_ad_keyword_themes: 0-10 score (3+ high-intent B2B keywords)
        existing_score: Current score (for trending)
        score_breakdown: Previous score breakdown for history

    Returns:
        dict with score, tier, breakdown, and signals
    """
    breakdown = score_breakdown or {}
    signals = {}

    # Funding signal (+40)
    if funding_signal:
        signals["funding_signal"] = True
        breakdown["funding"] = SCORE_WEIGHTS["funding"]
    else:
        signals["funding_signal"] = False
        breakdown["funding"] = 0

    # Hiring signal (+20)
    if hiring_signal:
        signals["hiring_signal"] = True
        breakdown["hiring"] = SCORE_WEIGHTS["hiring"]
    else:
        signals["hiring_signal"] = False
        breakdown["hiring"] = 0

    # Review signals
    if review_switching_intent:
        signals["review_signal"] = True
        breakdown["review"] = SCORE_WEIGHTS["review_switching"]
    elif review_signal:
        signals["review_signal"] = True
        breakdown["review"] = SCORE_WEIGHTS["review_negative"]
    else:
        signals["review_signal"] = False
        breakdown["review"] = 0

    # LinkedIn signals
    if linkedin_frustrated:
        signals["linkedin_signal"] = True
        breakdown["linkedin"] = SCORE_WEIGHTS["linkedin_frustrated"]
    elif linkedin_pain:
        signals["linkedin_signal"] = True
        breakdown["linkedin"] = SCORE_WEIGHTS["linkedin_pain"]
    else:
        signals["linkedin_signal"] = False
        breakdown["linkedin"] = 0

    # Technographic signal (+5)
    if technographic_signal:
        signals["technographic_signal"] = True
        breakdown["technographic"] = SCORE_WEIGHTS["technographic"]
    else:
        signals["technographic_signal"] = False
        breakdown["technographic"] = 0

    # Website visit (+30) — highest quality signal
    if website_visit:
        signals["website_visit_signal"] = True
        breakdown["website_visit"] = SCORE_WEIGHTS["website_visit"]
    else:
        signals["website_visit_signal"] = False
        breakdown["website_visit"] = 0

    # Meta Ad signals (cumulative, up to +55)
    meta_ad_contrib = get_meta_ad_score_contribution(
        is_active=meta_ad_active,
        intensity=meta_ad_intensity,
        is_lead_gen=meta_ad_lead_gen,
        recency=meta_ad_recency,
    )
    breakdown["meta_ad"] = meta_ad_contrib["points"]
    signals["meta_ad_signal"] = meta_ad_contrib["points"] > 0

    # Reddit signals (cumulative, up to +45)
    reddit_contrib = get_reddit_score_contribution(
        ad_active=reddit_ad_active,
        organic_active=reddit_organic_active,
        sentiment_label=reddit_sentiment_label,
        intensity=reddit_intensity,
    )
    breakdown["reddit"] = reddit_contrib["points"]
    signals["reddit_signal"] = reddit_contrib["points"] > 0

    # Google Ads signals (cumulative, up to +45)
    google_ad_contrib = get_google_ad_score_contribution(
        is_active=google_ad_active,
        intensity=google_ad_intensity,
        keyword_themes=google_ad_keyword_themes,
    )
    breakdown["google_ad"] = google_ad_contrib["points"]
    signals["google_ad_signal"] = google_ad_contrib["points"] > 0

    # Instagram signals (cumulative, up to +40)
    instagram_contrib = get_instagram_score_contribution(
        is_active=instagram_active,
        engagement=instagram_engagement,
        posting_frequency=instagram_posting_frequency,
        follower_growth=instagram_follower_growth,
    )
    breakdown["instagram"] = instagram_contrib["points"]
    signals["instagram_signal"] = instagram_contrib["points"] > 0

    # Calculate total score
    total = sum(breakdown.values())

    # Determine tier
    if total >= TIER_HOT:
        tier = "hot"
    elif total >= TIER_WARM:
        tier = "warm"
    else:
        tier = "cold"

    # Calculate trend (was the prospect already warming?)
    trend = "rising" if total > existing_score else ("falling" if total < existing_score else "stable")

    return {
        "score": float(total),
        "tier": tier,
        "trend": trend,
        "funding_signal": signals["funding_signal"],
        "hiring_signal": signals["hiring_signal"],
        "review_signal": signals["review_signal"],
        "linkedin_signal": signals["linkedin_signal"],
        "technographic_signal": signals["technographic_signal"],
        "website_visit_signal": signals["website_visit_signal"],
        "meta_ad_signal": signals["meta_ad_signal"],
        "reddit_signal": signals["reddit_signal"],
        "google_ad_signal": signals["google_ad_signal"],
        "instagram_signal": signals["instagram_signal"],
        "score_breakdown": breakdown,
        "last_updated_at": datetime.utcnow().isoformat(),
    }


def get_meta_ad_score_contribution(
    is_active: bool,
    intensity: int,
    is_lead_gen: bool,
    recency: int,
) -> dict:
    """
    Calculate meta_ad contribution to intent score and breakdown.
    Max contribution: 55 points (active=25 + intensity=15 + lead_gen=10 + recency=5).

    Args:
        is_active: Active Meta ads in last 30 days
        intensity: 0-25 score (3+ ads OR >21 days running)
        is_lead_gen: Lead gen ad format present
        recency: 0-15 score (days since first seen)
    """
    if not is_active:
        return {"points": 0, "breakdown": {}}

    points = 0
    breakdown = {}

    points += SCORE_WEIGHTS["meta_ad_active"]
    breakdown["meta_ad_active"] = SCORE_WEIGHTS["meta_ad_active"]

    if intensity >= 15:
        points += SCORE_WEIGHTS["meta_ad_intensity"]
        breakdown["meta_ad_intensity"] = SCORE_WEIGHTS["meta_ad_intensity"]

    if is_lead_gen:
        points += SCORE_WEIGHTS["meta_ad_lead_gen"]
        breakdown["meta_ad_lead_gen"] = SCORE_WEIGHTS["meta_ad_lead_gen"]

    if recency >= 3:
        points += SCORE_WEIGHTS["meta_ad_recency"]
        breakdown["meta_ad_recency"] = SCORE_WEIGHTS["meta_ad_recency"]

    return {"points": points, "breakdown": breakdown}


def get_reddit_score_contribution(
    ad_active: bool,
    organic_active: bool,
    sentiment_label: str,
    intensity: int,
) -> dict:
    """
    Calculate Reddit contribution to intent score and breakdown.
    Max contribution: 45 points (ad_active=20 + organic_active=10 + positive_sentiment=10 + intensity=5).

    Args:
        ad_active: Company advertising on Reddit
        organic_active: Active Reddit mentions (5+ posts)
        sentiment_label: "positive", "negative", or "neutral"
        intensity: 0-20 score (10+ mentions OR 50+ upvotes)
    """
    if not any([ad_active, organic_active, sentiment_label == "positive", intensity >= 10]):
        return {"points": 0, "breakdown": {}}

    points = 0
    breakdown = {}

    if ad_active:
        points += SCORE_WEIGHTS["reddit_ad_active"]
        breakdown["reddit_ad_active"] = SCORE_WEIGHTS["reddit_ad_active"]

    if organic_active:
        points += SCORE_WEIGHTS["reddit_organic_active"]
        breakdown["reddit_organic_active"] = SCORE_WEIGHTS["reddit_organic_active"]

    if sentiment_label == "positive":
        points += SCORE_WEIGHTS["reddit_positive_sentiment"]
        breakdown["reddit_positive_sentiment"] = SCORE_WEIGHTS["reddit_positive_sentiment"]

    if intensity >= 10:
        points += SCORE_WEIGHTS["reddit_intensity"]
        breakdown["reddit_intensity"] = SCORE_WEIGHTS["reddit_intensity"]

    return {"points": points, "breakdown": breakdown}


def get_google_ad_score_contribution(
    is_active: bool,
    intensity: int,
    keyword_themes: int,
) -> dict:
    """
    Calculate Google Ads contribution to intent score and breakdown.
    Max contribution: 45 points (active=20 + intensity=15 + keyword_themes=10).

    Args:
        is_active: Company is advertising on Google
        intensity: 0-15 score (3+ campaigns OR high ad volume)
        keyword_themes: 0-10 score (3+ high-intent B2B keywords)
    """
    if not is_active:
        return {"points": 0, "breakdown": {}}

    points = 0
    breakdown = {}

    points += SCORE_WEIGHTS["google_ad_active"]
    breakdown["google_ad_active"] = SCORE_WEIGHTS["google_ad_active"]

    if intensity >= 8:
        points += SCORE_WEIGHTS["google_ad_intensity"]
        breakdown["google_ad_intensity"] = SCORE_WEIGHTS["google_ad_intensity"]

    if keyword_themes >= 3:
        points += SCORE_WEIGHTS["google_ad_keyword_themes"]
        breakdown["google_ad_keyword_themes"] = SCORE_WEIGHTS["google_ad_keyword_themes"]

    return {"points": points, "breakdown": breakdown}


def get_instagram_score_contribution(
    is_active: bool,
    engagement: int,
    posting_frequency: int,
    follower_growth: int,
) -> dict:
    """
    Calculate Instagram contribution to intent score and breakdown.
    Max contribution: 40 points (active=15 + engagement=10 + frequency=10 + growth=5).

    Args:
        is_active: Company has an active Instagram presence
        engagement: 0-10 score (based on follower count as engagement proxy)
        posting_frequency: 0-10 score (based on post count)
        follower_growth: 0-5 score (established account >1000 followers)
    """
    if not is_active:
        return {"points": 0, "breakdown": {}}

    points = 0
    breakdown = {}

    points += SCORE_WEIGHTS["instagram_active"]
    breakdown["instagram_active"] = SCORE_WEIGHTS["instagram_active"]

    if engagement >= 5:
        points += SCORE_WEIGHTS["instagram_engagement"]
        breakdown["instagram_engagement"] = SCORE_WEIGHTS["instagram_engagement"]

    if posting_frequency >= 5:
        points += SCORE_WEIGHTS["instagram_posting_frequency"]
        breakdown["instagram_posting_frequency"] = SCORE_WEIGHTS["instagram_posting_frequency"]

    if follower_growth >= 3:
        points += SCORE_WEIGHTS["instagram_follower_growth"]
        breakdown["instagram_follower_growth"] = SCORE_WEIGHTS["instagram_follower_growth"]

    return {"points": points, "breakdown": breakdown}


def get_score_description(score: float, breakdown: dict) -> str:
    """
    Generate a human-readable description of why a prospect scored the way they did.
    Used for the dashboard to explain the score to sales reps.
    """
    parts = []
    if breakdown.get("funding"):
        parts.append(f"Raised funding (+{breakdown['funding']})")
    if breakdown.get("hiring"):
        parts.append(f"Active hiring (+{breakdown['hiring']})")
    if breakdown.get("review"):
        parts.append(f"Competitor issues (+{breakdown['review']})")
    if breakdown.get("linkedin"):
        parts.append(f"Expressed frustrations (+{breakdown['linkedin']})")
    if breakdown.get("technographic"):
        parts.append(f"Tech gap detected (+{breakdown['technographic']})")
    if breakdown.get("website_visit"):
        parts.append(f"Visited your site (+{breakdown['website_visit']})")
    if breakdown.get("meta_ad"):
        parts.append(f"Meta Ads active (+{breakdown['meta_ad']})")
    if breakdown.get("reddit"):
        parts.append(f"Reddit activity (+{breakdown['reddit']})")
    if breakdown.get("google_ad"):
        parts.append(f"Google Ads active (+{breakdown['google_ad']})")
    if breakdown.get("instagram"):
        parts.append(f"Instagram active (+{breakdown['instagram']})")

    if not parts:
        return "No strong signals detected yet. Keep monitoring."

    return f"Signals: {', '.join(parts)}"


def should_trigger_alert(prev_score: float, new_score: float, tier_changed: bool) -> dict:
    """
    Determine if a score change should trigger a notification.
    Based on Playbook Module 05: Behavioural Triggers.
    """
    triggers = []

    # Tier change triggers
    if tier_changed:
        if prev_score < 50 and new_score >= 50:
            triggers.append({
                "type": "tier_hot",
                "message": f"Prospect moved to HOT — score {new_score}",
                "priority": "high"
            })
        elif prev_score < 20 and new_score >= 20:
            triggers.append({
                "type": "tier_warm",
                "message": f"Prospect moved to WARM — score {new_score}",
                "priority": "medium"
            })

    # Significant score jump (+15 or more in one update)
    if new_score - prev_score >= 15:
        triggers.append({
            "type": "score_spike",
            "message": f"Significant signal detected — score jumped +{new_score - prev_score}",
            "priority": "medium"
        })

    # New funding signal
    if "funding" in triggers or (new_score >= 40 and prev_score < 40):
        triggers.append({
            "type": "funding_alert",
            "message": "Company just raised funding — likely evaluating new tools",
            "priority": "high"
        })

    return {
        "should_alert": len(triggers) > 0,
        "triggers": triggers,
    }
