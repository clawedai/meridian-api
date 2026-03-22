"""Rate limiting using sliding window algorithm."""
import asyncio
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, status
from starlette.requests import Request

from ..api.deps import get_current_user

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    requests_per_minute: int
    window_seconds: int = 60


# Tier rate limits (requests per minute)
TIER_LIMITS = {
    None: 60,       # Free
    "starter": 120,
    "growth": 300,
    "scale": 1000,
}

# Load from env if set
def _get_rate_limit(tier: Optional[str]) -> int:
    env_key = f"RATE_LIMIT_{tier.upper() if tier else 'FREE'}"
    return int(os.getenv(env_key, str(TIER_LIMITS.get(tier, 60))))


@dataclass
class SlidingWindowCounter:
    timestamps: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def is_allowed(self, limit: int, window: int = 60) -> Tuple[bool, int, int]:
        """
        Thread-safe rate limit check using a two-phase reservation pattern.

        Phase 1: Reserve a slot atomically (inside lock) - counts before filtering
        Phase 2: Record timestamp - filters old entries and appends

        MUST be called within the lock or await acquire() before calling.
        """
        async with self.lock:
            # Phase 1: Count valid entries BEFORE modification
            # This is the critical part that prevents the burst-into-window issue
            # where all concurrent requests see the same timestamps and all pass
            current_time = time.time()
            window_start = current_time - window
            valid_timestamps = [ts for ts in self.timestamps if ts > window_start]
            count = len(valid_timestamps)

            if count < limit:
                # Reserve slot first (increment counter before filtering stale entries)
                count += 1
                # Phase 2: Filter stale AND append new timestamp atomically
                self.timestamps = [ts for ts in self.timestamps if ts > window_start] + [current_time]
                remaining = limit - count
                reset_time = int(window_start + window)
                return True, remaining, reset_time
            else:
                reset_time = int(min(valid_timestamps) + window) if valid_timestamps else int(current_time + window)
                return False, 0, reset_time


class RateLimiter:
    def __init__(self):
        self._counters: Dict[str, SlidingWindowCounter] = defaultdict(SlidingWindowCounter)
        self._cleanup_task: Optional[asyncio.Task] = None

    def start(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(300)
                self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Rate limiter cleanup error: {e}")

    def _cleanup(self):
        current = time.time()
        keys = []
        for k, v in self._counters.items():
            timestamps = v.timestamps  # Capture atomically to avoid race with max()
            if not timestamps or max(timestamps) < current - 600:
                keys.append(k)
        for k in keys:
            self._counters.pop(k, None)

    async def check(self, user_id: str, tier: Optional[str] = None) -> Tuple[bool, int, int, int]:
        limit = _get_rate_limit(tier)
        counter = self._counters[user_id]
        # is_allowed now handles its own lock internally
        allowed, remaining, reset = await counter.is_allowed(limit)
        return allowed, remaining, reset, limit


_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
        _rate_limiter.start()
    return _rate_limiter


async def rate_limit(
    current_user: dict = Depends(get_current_user),
    request: Request = None,
) -> dict:
    """Rate limit dependency. Add Depends(rate_limit) to endpoints."""
    if os.getenv("ENVIRONMENT") != "production" and os.getenv("RATE_LIMIT_BYPASS") == "true":
        return current_user

    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get user tier from header or default to None
    tier = request.headers.get("X-User-Tier") if request else None

    limiter = get_rate_limiter()
    try:
        allowed, remaining, reset, limit = await limiter.check(user_id, tier)

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {limit} requests/minute.",
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(reset),
                    "Retry-After": str(max(1, reset - int(time.time()))),
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rate limit error for {user_id}: {e}")
        # Graceful degradation - allow request on error

    return current_user
