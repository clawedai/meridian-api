"""Retry mechanisms with exponential backoff."""
import asyncio
import logging
import random
from functools import wraps
from typing import Callable, Optional, Set

import httpx

logger = logging.getLogger(__name__)


class RetryExhaustedError(Exception):
    def __init__(self, message: str, attempts: int, last_error: Optional[Exception] = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def _calculate_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Calculate delay with exponential backoff and jitter."""
    delay = min(base_delay * (2 ** attempt), max_delay)
    # Full jitter
    return random.uniform(0, delay)


def _is_retryable(exc: Exception, retry_on_5xx: bool = True, retry_on_429: bool = True) -> bool:
    """Check if exception is retryable."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status >= 500:
            return retry_on_5xx
        if status == 429:
            return retry_on_429
    return False


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retry_on_5xx: bool = True,
    retry_on_429: bool = True,
):
    """Decorator for retry with exponential backoff and jitter."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    return result
                except Exception as e:
                    last_error = e
                    if attempt >= max_retries:
                        break
                    if not _is_retryable(e, retry_on_5xx, retry_on_429):
                        raise
                    delay = _calculate_delay(attempt, base_delay, max_delay)
                    logger.warning(f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {e}")
                    await asyncio.sleep(delay)
            raise RetryExhaustedError(
                f"Failed after {max_retries + 1} attempts",
                attempts=max_retries + 1,
                last_error=last_error,
            ) from last_error
        return wrapper
    return decorator


# Specialized decorator for rate-limited APIs (OpenAI, Anthropic)
def retry_on_rate_limit(max_retries: int = 5, base_delay: float = 2.0):
    """Retry decorator optimized for rate-limited APIs."""
    return retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=120.0,
        retry_on_5xx=True,
        retry_on_429=True,
    )
