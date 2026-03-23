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
        "score_breakdown": breakdown,
        "last_updated_at": datetime.utcnow().isoformat(),
    }


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
