"""Health check service for Meridian API."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings


class HealthResult:
    """Result of a single health check."""

    def __init__(
        self,
        status: str,
        message: str,
        response_time_ms: float,
        timestamp: str | None = None,
    ):
        self.status = status  # 'healthy' | 'unhealthy' | 'degraded'
        self.message = message
        self.response_time_ms = response_time_ms
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "response_time_ms": round(self.response_time_ms, 2),
            "timestamp": self.timestamp,
        }


class HealthService:
    """Service for checking health of external dependencies."""

    DB_TIMEOUT = 5.0
    CACHE_TTL_SECONDS = 30.0
    _health_cache: dict[str, Any] | None = None
    _cache_timestamp: float = 0.0

    def _get_cached_result(self) -> dict[str, Any] | None:
        """Return cached health result if fresh, otherwise None."""
        if self._health_cache is None:
            return None
        if time.monotonic() - self._cache_timestamp > self.CACHE_TTL_SECONDS:
            return None
        return self._health_cache

    def _set_cached_result(self, result: dict[str, Any]) -> None:
        """Store health result in cache with current timestamp."""
        self._health_cache = result
        self._cache_timestamp = time.monotonic()

    async def check_supabase(self) -> HealthResult:
        """Check Supabase database connectivity with a simple query."""
        start = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=self.DB_TIMEOUT) as client:
                url = f"{settings.SUPABASE_URL}/rest/v1/"
                headers = {
                    "apikey": settings.SUPABASE_KEY,
                    "Authorization": f"Bearer {settings.SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "count=exact",
                }
                params = {"limit": 1}

                response = await client.get(url, headers=headers, params=params)
                elapsed_ms = (time.perf_counter() - start) * 1000

                if response.status_code in (200, 201, 206):
                    return HealthResult(
                        status="healthy",
                        message="Supabase connection successful",
                        response_time_ms=elapsed_ms,
                    )
                elif response.status_code == 401:
                    return HealthResult(
                        status="degraded",
                        message=f"Supabase auth failed: {response.status_code}",
                        response_time_ms=elapsed_ms,
                    )
                else:
                    return HealthResult(
                        status="unhealthy",
                        message=f"Supabase returned status {response.status_code}",
                        response_time_ms=elapsed_ms,
                    )

        except httpx.TimeoutException:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return HealthResult(
                status="unhealthy",
                message="Supabase connection timed out",
                response_time_ms=elapsed_ms,
            )
        except httpx.ConnectError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return HealthResult(
                status="unhealthy",
                message="Failed to connect to Supabase",
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return HealthResult(
                status="unhealthy",
                message=f"Supabase check failed: {str(e)}",
                response_time_ms=elapsed_ms,
            )

    async def check_anthropic(self) -> HealthResult:
        """Check Anthropic API key format (lightweight check)."""
        start = time.perf_counter()
        elapsed_ms = (time.perf_counter() - start) * 1000

        api_key = settings.ANTHROPIC_API_KEY

        if not api_key:
            return HealthResult(
                status="unhealthy",
                message="ANTHROPIC_API_KEY is not configured",
                response_time_ms=elapsed_ms,
            )

        # Verify Anthropic API key format (sk-ant-...)
        if not api_key.startswith("sk-ant-"):
            return HealthResult(
                status="unhealthy",
                message="Invalid Anthropic API key format",
                response_time_ms=elapsed_ms,
            )

        return HealthResult(
            status="healthy",
            message="Anthropic API key configured correctly",
            response_time_ms=elapsed_ms,
        )

    async def check_all(self) -> dict[str, Any]:
        """Run all health checks in parallel and return summary."""
        # Check cache first
        cached = self._get_cached_result()
        if cached is not None:
            return cached

        supabase_task = self.check_supabase()
        anthropic_task = self.check_anthropic()

        results = await asyncio.gather(supabase_task, anthropic_task, return_exceptions=True)

        supabase_result = results[0]
        anthropic_result = results[1]

        checks: dict[str, Any] = {}

        # Process Supabase result
        if isinstance(supabase_result, Exception):
            checks["supabase"] = HealthResult(
                status="unhealthy",
                message=f"Check failed with exception: {str(supabase_result)}",
                response_time_ms=0,
            ).to_dict()
        else:
            checks["supabase"] = supabase_result.to_dict()

        # Process Anthropic result
        if isinstance(anthropic_result, Exception):
            checks["anthropic"] = HealthResult(
                status="unhealthy",
                message=f"Check failed with exception: {str(anthropic_result)}",
                response_time_ms=0,
            ).to_dict()
        else:
            checks["anthropic"] = anthropic_result.to_dict()

        # Determine overall status
        overall_status = "healthy"
        for check_data in checks.values():
            if check_data["status"] == "unhealthy":
                overall_status = "unhealthy"
                break
            elif check_data["status"] == "degraded":
                overall_status = "degraded"

        result = {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "version": "1.0.0",
        }

        # Store in cache before returning
        self._set_cached_result(result)
        return result


# Singleton instance
health_service = HealthService()
