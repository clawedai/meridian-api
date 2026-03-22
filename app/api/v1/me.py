"""
User account and subscription endpoints for Drishti intelligence platform.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Dict, Any
import httpx

from ..deps import get_current_user, SupabaseClient, get_supabase, get_user_context
from ...services.tier_limits import TierService, TIER_LIMITS, get_tier_display_name

router = APIRouter(prefix="/me", tags=["Me"])


# Response Models
class SubscriptionSummary(BaseModel):
    tier: Optional[str]
    plan_name: str
    status: str
    current_period_end: Optional[str]


class UsageLimit(BaseModel):
    current: int
    limit: int


class UsageLimits(BaseModel):
    entities: UsageLimit
    sources: UsageLimit


class UserProfileResponse(BaseModel):
    id: str
    email: str
    subscription: SubscriptionSummary
    limits: UsageLimits


class DetailedSubscriptionResponse(BaseModel):
    tier: Optional[str]
    plan_name: str
    status: str
    current_period_start: Optional[str]
    current_period_end: Optional[str]
    cancel_at_period_end: bool
    payment_method: Optional[str]
    features: Dict[str, Any]


class UsageResponse(BaseModel):
    resources: Dict[str, UsageLimit]
    usage_percentage: Dict[str, float]


async def _get_subscription_details(
    user_id: str,
    user_token: str,
    supabase: SupabaseClient
) -> Dict[str, Any]:
    """Fetch detailed subscription information from Supabase."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            sub_response = await client.get(
                f"{supabase.url}/rest/v1/user_subscriptions",
                params={
                    "user_id": f"eq.{user_id}",
                    "select": "*",
                    "order": "created_at.desc",
                    "limit": "1"
                },
                headers={
                    "apikey": supabase.anon_key,
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                }
            )
            sub_response.raise_for_status()

            subscription = None
            if sub_response.status_code == 200:
                subs = sub_response.json()
                subscription = subs[0] if subs else None

            stripe_sub_id = None
            stripe_status = "unknown"
            period_start = None
            period_end = None
            cancel_at_period_end = False

            if subscription and subscription.get("stripe_subscription_id"):
                stripe_sub_id = subscription["stripe_subscription_id"]
                stripe_status = subscription.get("status", "active")
                period_start = subscription.get("current_period_start")
                period_end = subscription.get("current_period_end")
                cancel_at_period_end = subscription.get("cancel_at_period_end", False)

            return {
                "tier": subscription.get("tier") if subscription else None,
                "status": stripe_status,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "cancel_at_period_end": cancel_at_period_end,
                "payment_method": "card" if stripe_sub_id else None,
            }

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to fetch subscription details"
        )


async def _get_resource_usage(
    user_id: str,
    user_token: str,
    supabase: SupabaseClient
) -> Dict[str, int]:
    """Get current resource usage counts."""
    try:
        headers = {
            "apikey": supabase.anon_key,
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
        }

        usage = {}

        async with httpx.AsyncClient(timeout=30.0) as client:
            for table in ["entities", "sources", "alerts", "reports"]:
                r = await client.get(
                    f"{supabase.url}/rest/v1/{table}",
                    params={"user_id": f"eq.{user_id}", "select": "id"},
                    headers=headers
                )
                usage[table] = len(r.json()) if r.status_code == 200 else 0

        return usage

    except Exception:
        return {"entities": 0, "sources": 0, "alerts": 0, "reports": 0}


@router.get("", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    user_context: dict = Depends(get_user_context),
):
    """Get current user profile with subscription summary and limits."""
    user_id = current_user["id"]
    user_token = user_context["user_token"]

    subscription_info = await _get_subscription_details(user_id, user_token, supabase)

    tier_service = TierService(user_id=user_id, user_token=user_token)
    try:
        tier = await tier_service.get_user_tier()
    finally:
        await tier_service.close()

    usage = await _get_resource_usage(user_id, user_token, supabase)
    limits_config = TIER_LIMITS.get(tier, TIER_LIMITS[None])

    return UserProfileResponse(
        id=user_id,
        email=current_user["email"],
        subscription=SubscriptionSummary(
            tier=tier,
            plan_name=get_tier_display_name(tier),
            status=subscription_info["status"],
            current_period_end=subscription_info["current_period_end"],
        ),
        limits=UsageLimits(
            entities=UsageLimit(
                current=usage["entities"],
                limit=-1 if limits_config["entities"] == float('inf') else int(limits_config["entities"]),
            ),
            sources=UsageLimit(
                current=usage["sources"],
                limit=-1 if limits_config["sources"] == float('inf') else int(limits_config["sources"]),
            ),
        ),
    )


@router.get("/subscription", response_model=DetailedSubscriptionResponse)
async def get_subscription_details(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    user_context: dict = Depends(get_user_context),
):
    """Get detailed subscription status with features."""
    user_id = current_user["id"]
    user_token = user_context["user_token"]

    subscription_info = await _get_subscription_details(user_id, user_token, supabase)
    tier = subscription_info["tier"]

    tier_features = {
        None: {"entities_limit": 0, "sources_limit": 0, "has_api_access": False},
        "starter": {"entities_limit": 5, "sources_limit": 10, "has_api_access": False},
        "growth": {"entities_limit": 20, "sources_limit": 40, "has_api_access": True},
        "scale": {"entities_limit": -1, "sources_limit": -1, "has_api_access": True},
    }

    features = tier_features.get(tier, tier_features[None])

    return DetailedSubscriptionResponse(
        tier=tier,
        plan_name=get_tier_display_name(tier),
        status=subscription_info["status"],
        current_period_start=subscription_info["current_period_start"],
        current_period_end=subscription_info["current_period_end"],
        cancel_at_period_end=subscription_info["cancel_at_period_end"],
        payment_method=subscription_info["payment_method"],
        features=features,
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage_details(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    user_context: dict = Depends(get_user_context),
):
    """Get current usage vs plan limits."""
    user_id = current_user["id"]
    user_token = user_context["user_token"]

    tier_service = TierService(user_id=user_id, user_token=user_token)
    try:
        tier = await tier_service.get_user_tier()
    finally:
        await tier_service.close()

    usage = await _get_resource_usage(user_id, user_token, supabase)
    limits_config = TIER_LIMITS.get(tier, TIER_LIMITS[None])

    resources = {}
    usage_percentage = {}

    for resource in ["entities", "sources", "alerts", "reports"]:
        limit = limits_config.get(resource, 0)
        current = usage.get(resource, 0)
        effective_limit = -1 if limit == float('inf') else int(limit)

        resources[resource] = UsageLimit(current=current, limit=effective_limit)
        usage_percentage[resource] = 0.0 if effective_limit == -1 or effective_limit == 0 else round((current / effective_limit) * 100, 2)

    return UsageResponse(resources=resources, usage_percentage=usage_percentage)
