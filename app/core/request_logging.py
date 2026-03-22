"""
Request Logging Middleware for Drishti Intelligence Platform
"""

import time
import uuid
import logging
import jwt
from typing import Callable, List, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .logging import request_id_var, user_id_var, request_extra_var, get_api_logger
from .config import settings


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self.logger = get_api_logger()
        self.exclude_paths = exclude_paths or ["/health", "/healthz", "/ready", "/metrics", "/docs", "/openapi.json"]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self._should_exclude(request.url.path):
            return await call_next(request)

        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id_token = request_id_var.set(request_id)

        user_id = self._extract_user_id(request)
        user_id_token = user_id_var.set(user_id) if user_id else None

        request_extra = {
            "method": request.method,
            "path": str(request.url.path),
            "client_ip": self._get_client_ip(request),
        }
        request_extra_token = request_extra_var.set(request_extra)

        start_time = time.perf_counter()

        self.logger.info(
            "Request started",
            extra={"event": "request_start", "http_method": request.method, "path": str(request.url.path)},
        )

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000
            response.headers["X-Request-ID"] = request_id

            self.logger.info(
                "Request completed",
                extra={
                    "event": "request_end",
                    "http_method": request.method,
                    "path": str(request.url.path),
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )

            if duration_ms > 1000:
                self.logger.warning(
                    "Slow request detected",
                    extra={"event": "slow_request", "duration_ms": round(duration_ms, 2)},
                )

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.error(
                f"Request failed: {type(e).__name__}",
                extra={
                    "event": "request_exception",
                    "http_method": request.method,
                    "path": str(request.url.path),
                    "duration_ms": round(duration_ms, 2),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

        finally:
            request_id_var.reset(request_id_token)
            if user_id_token:
                user_id_var.reset(user_id_token)
            request_extra_var.reset(request_extra_token)

    def _should_exclude(self, path: str) -> bool:
        for excluded in self.exclude_paths:
            if path.startswith(excluded):
                return True
        return False

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        if request.client:
            return request.client.host
        return "unknown"

    def _extract_user_id(self, request: Request) -> Optional[str]:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        try:
            token = auth_header[7:]
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_signature": True, "verify_exp": True},
            )
            return payload.get("sub") or payload.get("user_id") or payload.get("uid")
        except jwt.ExpiredSignatureError:
            self.logger.debug("JWT token has expired")
        except jwt.InvalidTokenError as e:
            self.logger.debug(f"Invalid JWT token: {e}")
        except Exception as e:
            self.logger.debug(f"JWT parsing error: {e}")

        return None
