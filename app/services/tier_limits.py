"""
Tier and plan limits enforcement for Drishti intelligence platform.
"""

import logging
from typing import Optional, Tuple

import httpx
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from ..api.deps import get_user_context

logger = logging.getLogger(__name__)

# Tier limits configuration
TIER_LIMITS = {
    'starter': {'entities': 5, 'sources': 10, 'alerts': 5, 'reports': 10},
    'growth': {'entities': 20, 'sources': 40, 'alerts': 20, 'reports': 50},
    'scale': {'entities': float('inf'), 'sources': float('inf'), 'alerts': float('inf'), 'reports': float('inf')},
    None: {'entities': 0, 'sources': 0, 'alerts': 0, 'reports': 0},
}

TIER_HIERARCHY = {None: 0, 'starter': 1, 'growth': 2, 'scale': 3}


class TierLimits(BaseModel):
    entities: float
    sources: float
    alerts: float
    reports: float


class LimitCheckResult(BaseModel):
    allowed: bool
    current: int
    limit: float
    resource: str


class TierService:
    def __init__(self, user_id: str, user_token: str, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None):
        from app.core.config import settings
        self.user_id = user_id
        self.user_token = user_token
        self.supabase_url = supabase_url or settings.SUPABASE_URL
        # Use anon key — user_id is already validated by get_current_user dependency,
        # so RLS policies (user_id=eq.{user_id}) ensure users only see their own data
        self.anon_key = supabase_key or settings.SUPABASE_KEY
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.supabase_url,
                headers={
                    "Authorization": f"Bearer {self.anon_key}",
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def get_user_tier(self) -> Optional[str]:
        try:
            response = await self.client.get(
                "/rest/v1/user_subscriptions",
                params={"user_id": f"eq.{self.user_id}", "select": "tier", "order": "created_at.desc", "limit": "1"},
            )
            response.raise_for_status()
            data = response.json()

            if not data:
                return None

            return data[0].get("tier")

        except Exception as e:
            logger.error(f"Error fetching tier for user {self.user_id}: {e}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to verify subscription status")

    def _get_tier_limits(self, tier: Optional[str]) -> TierLimits:
        limits = TIER_LIMITS.get(tier, TIER_LIMITS[None])
        return TierLimits(**limits)

    async def get_user_limits(self) -> TierLimits:
        tier = await self.get_user_tier()
        return self._get_tier_limits(tier)

    async def _get_resource_count(self, table_name: str) -> int:
        try:
            response = await self.client.get(
                f"/rest/v1/{table_name}",
                params={"user_id": f"eq.{self.user_id}", "select": "id", "count": "exact"},
            )
            response.raise_for_status()

            total_count = response.headers.get("content-range")
            if total_count:
                parts = total_count.split("/")
                if len(parts) == 2:
                    return int(parts[1])

            data = response.json()
            return len(data) if data else 0

        except Exception as e:
            logger.warning(f"Error fetching {table_name} count: {e}")
            return 0

    async def check_entity_limit(self) -> Tuple[bool, int, int]:
        tier = await self.get_user_tier()
        limits = self._get_tier_limits(tier)
        limit = limits.entities

        if limit == float('inf'):
            return True, 0, float('inf')

        current = await self._get_resource_count("entities")
        return current < limit, current, limit

    async def check_source_limit(self) -> Tuple[bool, int, int]:
        tier = await self.get_user_tier()
        limits = self._get_tier_limits(tier)
        limit = limits.sources

        if limit == float('inf'):
            return True, 0, float('inf')

        current = await self._get_resource_count("sources")
        return current < limit, current, limit

    async def check_alert_limit(self) -> Tuple[bool, int, int]:
        tier = await self.get_user_tier()
        limits = self._get_tier_limits(tier)
        limit = limits.alerts

        if limit == float('inf'):
            return True, 0, float('inf')

        current = await self._get_resource_count("alerts")
        return current < limit, current, limit

    async def enforce_entity_limit(self) -> bool:
        allowed, current, limit = await self.check_entity_limit()

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "entity_limit_exceeded",
                    "message": f"Entity limit reached. You have {current} entities and your plan allows {limit}.",
                    "current": current,
                    "limit": limit,
                    "upgrade_url": "/billing/upgrade",
                },
            )
        return True

    async def enforce_source_limit(self, entity_id: Optional[str] = None) -> bool:
        allowed, current, limit = await self.check_source_limit()

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "source_limit_exceeded",
                    "message": f"Source limit reached. You have {current} sources and your plan allows {limit}.",
                    "current": current,
                    "limit": limit,
                    "upgrade_url": "/billing/upgrade",
                },
            )
        return True


def create_tier_service(user_id: str, user_token: str) -> TierService:
    return TierService(user_id=user_id, user_token=user_token)


def require_tier(required_tier: str):
    required_level = TIER_HIERARCHY.get(required_tier)
    if required_level is None:
        raise ValueError(f"Invalid tier: {required_tier}")

    async def _require_tier(user_id: str = Depends(lambda: None), user_token: str = Depends(lambda: None)) -> TierLimits:
        if not user_id or not user_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        service = TierService(user_id=user_id, user_token=user_token)
        try:
            tier = await service.get_user_tier()
            current_level = TIER_HIERARCHY.get(tier, 0)

            if current_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "insufficient_tier",
                        "message": f"This feature requires the '{required_tier}' tier or higher.",
                        "current_tier": tier,
                        "required_tier": required_tier,
                        "upgrade_url": "/billing/upgrade",
                    },
                )
            return await service.get_user_limits()
        finally:
            await service.close()

    return _require_tier


async def require_entity_limit(user_context: dict = Depends(get_user_context)) -> bool:
    user_id = user_context["user_id"]
    user_token = user_context["user_token"]

    service = TierService(user_id=user_id, user_token=user_token)
    try:
        return await service.enforce_entity_limit()
    finally:
        await service.close()


async def require_source_limit(user_context: dict = Depends(get_user_context)) -> bool:
    user_id = user_context["user_id"]
    user_token = user_context["user_token"]

    service = TierService(user_id=user_id, user_token=user_token)
    try:
        return await service.enforce_source_limit()
    finally:
        await service.close()


def get_tier_display_name(tier: Optional[str]) -> str:
    display_names = {None: "Free", 'starter': "Starter", 'growth': "Growth", 'scale': "Scale"}
    return display_names.get(tier, "Unknown")
