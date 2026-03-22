"""
Sentry Error Monitoring for Drishti Intelligence Platform
"""

import sentry_sdk
from sentry_sdk.integrations.httpx import HttpxIntegration

from .config import settings


def init_sentry() -> None:
    """Initialize Sentry with Drishti configuration."""
    if not settings.SENTRY_DSN:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            HttpxIntegration(),
        ],
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        release=settings.VERSION,
        environment=settings.ENVIRONMENT,
        server_name=settings.PROJECT_NAME,
        before_send=scrub_sensitive_data,
        attach_stacktrace=True,
        max_breadcrumbs=100,
    )


def scrub_sensitive_data(event: dict, hint: dict) -> dict:
    """Remove sensitive data from Sentry events."""
    SENSITIVE_FIELDS = {
        "password", "secret", "token", "api_key", "access_token",
        "refresh_token", "authorization", "supabase_key", "supabase_service_key",
        "stripe_key", "stripe_secret_key", "resend_api_key", "anthropic_api_key",
    }

    def recursive_scrub(obj: any, depth: int = 0) -> any:
        if depth > 10:
            return obj
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if any(f in k.lower() for f in SENSITIVE_FIELDS)
                else recursive_scrub(v, depth + 1)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [recursive_scrub(item, depth + 1) for item in obj]
        return obj

    if "data" in event:
        event["data"] = recursive_scrub(event["data"])
    if "request" in event and "headers" in event["request"]:
        headers = event["request"]["headers"]
        event["request"]["headers"] = {
            k: "[REDACTED]" if any(s in k.lower() for s in ["authorization", "cookie", "x-api-key"])
            else v for k, v in headers.items()
        }
    if "extra" in event:
        event["extra"] = recursive_scrub(event["extra"])

    return event


def capture_exception_with_context(exception: Exception, context: dict = None, user_id: str = None) -> str:
    """Capture an exception with additional context."""
    with sentry_sdk.push_scope() as scope:
        if context:
            for key, value in context.items():
                scope.set_extra(key, value)
        if user_id:
            scope.set_user({"id": user_id})
        return sentry_sdk.capture_exception(exception)


def set_user_context(user_id: str, email: str = None) -> None:
    """Set the current user context for Sentry."""
    user_context = {"id": user_id}
    if email:
        user_context["email"] = email
    sentry_sdk.set_user(user_context)


def add_breadcrumb(message: str, category: str = None, data: dict = None) -> None:
    """Add a breadcrumb to the current transaction."""
    sentry_sdk.add_breadcrumb(message=message, category=category, data=data or {})
