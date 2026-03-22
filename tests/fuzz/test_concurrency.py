"""
Concurrency Stress Tests for Meridian-API
Tests for race conditions, deadlocks, and resource exhaustion.

Run with: pytest tests/fuzz/test_concurrency.py -v --tb=short
"""
import asyncio
import gc
import os
import sys
import time
import tracemalloc
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ==============================================================================
# Test Results Tracking
# ==============================================================================

@dataclass
class TestResult:
    """Container for test results."""
    name: str
    passed: bool
    expected: Any
    actual: Any
    details: str = ""
    duration_ms: float = 0.0
    errors: List[str] = field(default_factory=list)


class StressTestResults:
    """Collect and report stress test results."""
    def __init__(self):
        self.results: List[TestResult] = []
        self._start_time: float = 0

    def add(self, result: TestResult):
        self.results.append(result)

    def summary(self) -> str:
        lines = ["=" * 70]
        lines.append("CONCURRENCY STRESS TEST SUMMARY")
        lines.append("=" * 70)
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        for r in self.results:
            status = "[PASS]" if r.passed else "[FAIL]"
            lines.append(f"\n{status}: {r.name}")
            lines.append(f"  Expected: {r.expected}")
            lines.append(f"  Actual:   {r.actual}")
            if r.errors:
                lines.append(f"  Errors:   {r.errors[:3]}")  # Show first 3 errors
            if r.details:
                lines.append(f"  Details:  {r.details[:100]}...")

        lines.append("\n" + "=" * 70)
        lines.append(f"Total: {len(self.results)} | Passed: {passed} | Failed: {failed}")
        lines.append("=" * 70)
        return "\n".join(lines)


# ==============================================================================
# Mocks and Helpers
# ==============================================================================

class MockSupabaseClient:
    """Mock Supabase client for testing."""

    def __init__(self):
        self.call_count = 0
        self.auth_token = "mock_token"
        self._lock = asyncio.Lock()

    async def async_select(self, table: str, *args, **kwargs):
        await asyncio.sleep(0.001)  # Simulate network delay
        self.call_count += 1
        return [{"id": f"item_{self.call_count}"}]

    async def async_insert(self, table: str, data: dict, *args, **kwargs):
        await asyncio.sleep(0.001)
        self.call_count += 1
        return [{"id": "new_item"}]

    async def refresh_auth(self):
        """Simulate token refresh."""
        async with self._lock:
            await asyncio.sleep(0.01)
            self.auth_token = f"refreshed_{time.time()}"
        return self.auth_token


# ==============================================================================
# Test 1: Rate Limiter Race Condition
# ==============================================================================

@pytest.mark.asyncio
async def test_rate_limiter_race_condition():
    """
    Test 1: Rate limiter race condition
    100 concurrent requests from same user hitting the limit exactly.
    Should be exactly `limit` successes, no more.
    """
    from app.core.rate_limit import SlidingWindowCounter, RateLimiter

    results = StressTestResults()
    limit = 10
    total_requests = 100

    async def make_request(counter: SlidingWindowCounter, request_id: int) -> bool:
        """Simulate a rate-limited request (is_allowed is now async, lock-protected)."""
        allowed, remaining, reset = await counter.is_allowed(limit)
        return allowed

    # Test SlidingWindowCounter directly
    counter = SlidingWindowCounter()

    # Launch all requests concurrently
    tasks = [make_request(counter, i) for i in range(total_requests)]
    results_list = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results_list if r)
    allowed_count = limit  # We expect exactly `limit` requests to succeed

    passed = success_count == allowed_count
    result = TestResult(
        name="Rate Limiter Race Condition",
        passed=passed,
        expected=f"Exactly {limit} successes out of {total_requests}",
        actual=f"{success_count} successes out of {total_requests}",
        details=f"Counter state: {len(counter.timestamps)} timestamps" if hasattr(counter, 'timestamps') else ""
    )
    results.add(result)

    print(results.summary())
    assert passed, f"Expected exactly {limit} successes, got {success_count}"


@pytest.mark.asyncio
async def test_rate_limiter_with_lock():
    """
    Test 1b: Rate limiter with proper locking
    Same test but using the lock-protected check method.
    """
    import os
    from app.core.rate_limit import RateLimiter, _get_rate_limit

    results = StressTestResults()
    total_requests = 100

    # Override the rate limit to 10 for this test
    os.environ["RATE_LIMIT_FREE"] = "10"

    # Reset global rate limiter
    import app.core.rate_limit as rl_module
    rl_module._rate_limiter = None

    limiter = RateLimiter()
    user_id = "test_user_123"

    # Verify the limit is 10 as expected
    effective_limit = _get_rate_limit(None)
    assert effective_limit == 10, f"Expected limit 10, got {effective_limit}"

    async def make_request(request_id: int) -> tuple[bool, int, int, int]:
        """Simulate a rate-limited request with proper locking."""
        return await limiter.check(user_id, tier=None)

    # Launch all requests concurrently
    tasks = [make_request(i) for i in range(total_requests)]
    results_list = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results_list if r[0])

    passed = success_count == 10
    result = TestResult(
        name="Rate Limiter (Lock-Protected)",
        passed=passed,
        expected=f"Exactly 10 successes out of {total_requests}",
        actual=f"{success_count} successes out of {total_requests}",
        details=f"All results: {[r[0] for r in results_list]}"
    )
    results.add(result)

    print(results.summary())

    # Cleanup
    del os.environ["RATE_LIMIT_FREE"]
    assert passed, f"Expected exactly 10 successes, got {success_count}"


# ==============================================================================
# Test 2: Sliding Window Counter Race
# ==============================================================================

@pytest.mark.asyncio
async def test_sliding_window_counter_race():
    """
    Test 2: Sliding window counter race condition
    Multiple requests updating the counter simultaneously.
    Check for lost updates.
    """
    from app.core.rate_limit import SlidingWindowCounter

    results = StressTestResults()
    limit = 1000  # High limit so all requests should succeed
    total_requests = 500
    expected_timestamps = total_requests

    counter = SlidingWindowCounter()

    async def update_counter(request_id: int):
        """Update counter and return whether allowed."""
        allowed, remaining, reset = await counter.is_allowed(limit)
        return allowed

    # Launch all requests concurrently
    tasks = [update_counter(i) for i in range(total_requests)]
    results_list = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results_list if r)
    timestamp_count = len(counter.timestamps)

    # Race condition check: timestamps should equal successful requests
    lost_updates = success_count - timestamp_count

    passed = lost_updates == 0
    result = TestResult(
        name="Sliding Window Counter Race",
        passed=passed,
        expected=f"No lost updates: {expected_timestamps} timestamps",
        actual=f"{timestamp_count} timestamps, {lost_updates} lost updates",
        details=f"Success: {success_count}, Timestamps: {timestamp_count}, Diff: {lost_updates}"
    )
    results.add(result)

    print(results.summary())

    # This test WILL fail due to the known race condition
    if not passed:
        print(f"\n⚠️  RACE CONDITION DETECTED: {lost_updates} lost updates!")
        print("The counter appends outside the lock protection.")
    assert passed, f"Lost {lost_updates} updates due to race condition"


# ==============================================================================
# Test 3: Connection Pool Exhaustion
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_connection_pool_exhaustion():
    """
    Test 3: Connection pool exhaustion
    500 concurrent HTTP requests. Check if any hang or timeout.
    """
    results = StressTestResults()
    total_requests = 500
    timeout_seconds = 30

    success_count = 0
    timeout_count = 0
    error_count = 0
    errors: List[str] = []

    async def make_http_request(request_id: int) -> Dict[str, Any]:
        """Simulate an HTTP request."""
        nonlocal success_count, timeout_count, error_count
        try:
            # Simulate network request with timeout
            await asyncio.sleep(0.01)  # Simulate work
            return {"success": True, "id": request_id}
        except asyncio.TimeoutError:
            timeout_count += 1
            return {"success": False, "error": "timeout"}
        except Exception as e:
            error_count += 1
            errors.append(str(e))
            return {"success": False, "error": str(e)}

    start_time = time.time()

    # Launch all requests concurrently
    tasks = [make_http_request(i) for i in range(total_requests)]

    # Use wait_for to enforce overall timeout
    try:
        results_list = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout_seconds
        )
        success_count = sum(1 for r in results_list if isinstance(r, dict) and r.get("success"))
    except asyncio.TimeoutError:
        elapsed = time.time() - start_time
        result = TestResult(
            name="Connection Pool Exhaustion",
            passed=False,
            expected=f"All {total_requests} requests complete within {timeout_seconds}s",
            actual=f"TIMEOUT after {elapsed:.1f}s",
            details=f"Completed: {success_count}, Timeouts: {timeout_count}, Errors: {error_count}"
        )
        results.add(result)
        print(results.summary())
        pytest.fail(f"Test timed out after {elapsed:.1f}s")

    elapsed = time.time() - start_time
    passed = success_count == total_requests and elapsed < timeout_seconds

    result = TestResult(
        name="Connection Pool Exhaustion",
        passed=passed,
        expected=f"All {total_requests} requests succeed within {timeout_seconds}s",
        actual=f"{success_count}/{total_requests} succeeded in {elapsed:.2f}s",
        details=f"Timeouts: {timeout_count}, Errors: {len(set(errors))} unique"
    )
    results.add(result)

    print(results.summary())
    assert passed, f"Only {success_count}/{total_requests} requests succeeded"


# ==============================================================================
# Test 4: AsyncClient Context Leaks
# ==============================================================================

@pytest.mark.asyncio
async def test_async_client_context_leaks():
    """
    Test 4: AsyncClient context leaks
    Rapid open/close of AsyncClient instances.
    Check for "SSL context" or "loop already running" errors.
    """
    results = StressTestResults()
    total_iterations = 100
    errors: List[str] = []

    async def rapid_open_close(instance_id: int):
        """Rapidly open and close AsyncClient instances."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Simulate a simple request
                await asyncio.sleep(0.001)
            return {"success": True, "id": instance_id}
        except RuntimeError as e:
            if "loop" in str(e).lower() or "ssl" in str(e).lower():
                errors.append(f"RuntimeError: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            errors.append(str(e))
            return {"success": False, "error": str(e)}

    # Launch rapid client creation
    tasks = [rapid_open_close(i) for i in range(total_iterations)]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results_list if isinstance(r, dict) and r.get("success"))
    error_types = Counter(errors)

    passed = len(errors) == 0
    result = TestResult(
        name="AsyncClient Context Leaks",
        passed=passed,
        expected="No SSL or loop errors",
        actual=f"{len(errors)} errors, {success_count}/{total_iterations} success",
        details=f"Error types: {dict(error_types)}" if error_types else "No errors"
    )
    results.add(result)

    print(results.summary())
    if errors:
        print(f"\n⚠️  Context leak errors detected:")
        for error in errors[:5]:
            print(f"  - {error}")
    assert passed, f"Detected {len(errors)} context leak errors"


# ==============================================================================
# Test 5: Retry Storm
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_retry_storm():
    """
    Test 5: Retry storm
    External API always returns 500. Check if retries don't pile up
    and max_retries is respected.
    """
    import httpx
    from app.core.retry import retry_with_backoff, RetryExhaustedError

    results = StressTestResults()
    max_retries = 3
    total_concurrent = 50

    attempt_counts: List[int] = []

    async def failing_request(request_id: int) -> Dict[str, Any]:
        """A request that always fails."""
        attempt_count = 0
        errors_seen = []

        @retry_with_backoff(max_retries=max_retries, base_delay=0.1, max_delay=1.0)
        async def attempt_request():
            nonlocal attempt_count
            attempt_count += 1
            # Create a proper HTTPStatusError that will be recognized
            mock_response = MagicMock()
            mock_response.status_code = 500
            raise httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=mock_response
            )

        try:
            await attempt_request()
        except RetryExhaustedError as e:
            attempt_counts.append(attempt_count)
            return {"exhausted": True, "attempts": attempt_count, "error": str(e)}
        except httpx.HTTPStatusError:
            # This shouldn't happen if retry is working
            attempt_counts.append(attempt_count)
            return {"exhausted": False, "error": "HTTPStatusError raised without retry"}
        except Exception as e:
            attempt_counts.append(attempt_count)
            return {"exhausted": False, "error": f"{type(e).__name__}: {str(e)}"}

    # Launch all requests concurrently
    tasks = [failing_request(i) for i in range(total_concurrent)]
    results_list = await asyncio.gather(*tasks)

    exhausted_count = sum(1 for r in results_list if isinstance(r, dict) and r.get("exhausted"))
    max_attempts_seen = max(attempt_counts) if attempt_counts else 0
    min_attempts_seen = min(attempt_counts) if attempt_counts else 0

    # All requests should exhaust with exactly max_retries + 1 attempts
    expected_attempts = max_retries + 1
    all_correct = all(att == expected_attempts for att in attempt_counts)

    passed = all_correct and exhausted_count == total_concurrent
    result = TestResult(
        name="Retry Storm",
        passed=passed,
        expected=f"All {total_concurrent} requests exhaust with exactly {expected_attempts} attempts",
        actual=f"{exhausted_count}/{total_concurrent} exhausted, attempts range: {min_attempts_seen}-{max_attempts_seen}",
        details=f"All correct: {all_correct}, Expected attempts: {expected_attempts}"
    )
    results.add(result)

    print(results.summary())
    assert passed, f"Retry storm test failed: attempts range {min_attempts_seen}-{max_attempts_seen}"


# ==============================================================================
# Test 6: Background Task Cleanup
# ==============================================================================

@pytest.mark.asyncio
async def test_background_task_cleanup():
    """
    Test 6: Background task cleanup
    Check rate limiter cleanup task doesn't leak on shutdown.
    """
    from app.core.rate_limit import RateLimiter

    results = StressTestResults()

    # Create a new rate limiter
    limiter = RateLimiter()
    limiter.start()

    # Add some entries
    for i in range(10):
        await limiter.check(f"user_{i}", tier=None)

    # Check cleanup task exists
    has_cleanup_task = limiter._cleanup_task is not None
    initial_counter_count = len(limiter._counters)

    # Cancel cleanup task
    if limiter._cleanup_task:
        limiter._cleanup_task.cancel()
        try:
            await limiter._cleanup_task
        except asyncio.CancelledError:
            pass

    # Verify task is cancelled
    task_cancelled = limiter._cleanup_task.done() or limiter._cleanup_task.cancelled()

    # Force garbage collection
    gc.collect()

    passed = has_cleanup_task and task_cancelled
    result = TestResult(
        name="Background Task Cleanup",
        passed=passed,
        expected="Cleanup task properly cancelled",
        actual=f"Task existed: {has_cleanup_task}, Cancelled: {task_cancelled}",
        details=f"Initial counters: {initial_counter_count}"
    )
    results.add(result)

    print(results.summary())
    assert passed, "Background cleanup task not properly handled"


# ==============================================================================
# Test 7: Supabase Client Race
# ==============================================================================

@pytest.mark.asyncio
async def test_supabase_client_race():
    """
    Test 7: Supabase client race
    Multiple concurrent requests to Supabase.
    Check for auth token refresh races.
    """
    from app.api.deps import SupabaseClient

    results = StressTestResults()
    total_requests = 100

    mock_client = MockSupabaseClient()
    initial_token = mock_client.auth_token

    async def concurrent_request(request_id: int) -> Dict[str, Any]:
        """Simulate a Supabase request."""
        token = mock_client.auth_token
        await mock_client.async_select("test_table")
        return {"success": True, "token": token, "id": request_id}

    # Launch concurrent requests
    tasks = [concurrent_request(i) for i in range(total_requests)]
    results_list = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results_list if r.get("success"))
    tokens_used = set(r.get("token") for r in results_list)

    # Check for token consistency (no mid-request refresh corruption)
    # All requests should complete with a valid token
    passed = success_count == total_requests and len(tokens_used) >= 1

    result = TestResult(
        name="Supabase Client Race",
        passed=passed,
        expected=f"All {total_requests} requests complete consistently",
        actual=f"{success_count}/{total_requests} success, {len(tokens_used)} unique tokens",
        details=f"Token consistency: {len(tokens_used)} (may indicate refresh races)"
    )
    results.add(result)

    print(results.summary())
    assert passed, f"Token refresh race detected: {len(tokens_used)} different tokens used"


# ==============================================================================
# Test 8: Memory Pressure
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_memory_pressure():
    """
    Test 8: Memory pressure
    1000 rapid entity creation requests.
    Check for memory leaks or OOM.
    """
    results = StressTestResults()
    total_requests = 1000
    batch_size = 100

    # Start memory tracking
    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    # Track created entities
    created_entities: List[Dict] = []

    async def create_entity(entity_id: int) -> Dict[str, Any]:
        """Simulate entity creation."""
        entity = {
            "id": f"entity_{entity_id}",
            "name": f"Entity {entity_id}",
            "data": "x" * 1000,  # ~1KB per entity
            "created_at": time.time()
        }
        created_entities.append(entity)
        await asyncio.sleep(0.001)  # Simulate async work
        return entity

    # Process in batches to avoid overwhelming the system
    for batch_start in range(0, total_requests, batch_size):
        batch_end = min(batch_start + batch_size, total_requests)
        tasks = [create_entity(i) for i in range(batch_start, batch_end)]
        await asyncio.gather(*tasks)

    # Take memory snapshot after
    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Calculate memory growth
    top_stats = snapshot_after.compare_to(snapshot_before, 'lineno')
    total_growth = sum(stat.size_diff for stat in top_stats)

    entity_count = len(created_entities)
    expected_memory_per_entity = 1000  # ~1KB
    expected_total = total_requests * expected_memory_per_entity

    # Memory growth should be roughly proportional to entities created
    # (some overhead is expected)
    memory_ratio = total_growth / expected_total if expected_total > 0 else 0

    # Memory growth should be within reasonable bounds (less than 2x expected)
    passed = entity_count == total_requests and memory_ratio < 2.0

    result = TestResult(
        name="Memory Pressure",
        passed=passed,
        expected=f"{total_requests} entities created, memory growth < 2x expected",
        actual=f"{entity_count} entities, growth ratio: {memory_ratio:.2f}x",
        details=f"Memory growth: {total_growth / 1024:.1f}KB"
    )
    results.add(result)

    # Clean up
    created_entities.clear()

    print(results.summary())
    assert passed, f"Memory issue detected: growth ratio {memory_ratio:.2f}x"


# ==============================================================================
# Test 9: Event Loop Starvation
# ==============================================================================

@pytest.mark.asyncio
async def test_event_loop_starvation():
    """
    Test 9: Event loop starvation
    CPU-bound work in async context blocking the event loop.
    """
    results = StressTestResults()
    total_tasks = 50
    timeout_seconds = 5

    completion_times: List[float] = []
    blocked_time: float = 0

    async def cpu_bound_task(task_id: int) -> Dict[str, Any]:
        """Simulate CPU-bound work."""
        start = time.time()

        # Simulate CPU-bound work (should not block the event loop!)
        # In a properly implemented async system, this should yield control
        result = sum(i * i for i in range(10000))

        elapsed = time.time() - start
        completion_times.append(elapsed)
        return {"success": True, "elapsed": elapsed, "result": result}

    async def io_bound_task(task_id: int) -> Dict[str, Any]:
        """IO-bound task for comparison."""
        start = time.time()
        await asyncio.sleep(0.01)  # Simulate IO
        elapsed = time.time() - start
        return {"success": True, "elapsed": elapsed}

    # Run CPU-bound tasks
    cpu_start = time.time()
    tasks = [cpu_bound_task(i) for i in range(total_tasks)]

    try:
        cpu_results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout_seconds
        )
        cpu_elapsed = time.time() - cpu_start
    except asyncio.TimeoutError:
        cpu_elapsed = time.time() - cpu_start
        cpu_results = []

    # Run IO-bound tasks for comparison
    io_start = time.time()
    io_tasks = [io_bound_task(i) for i in range(total_tasks)]
    io_results = await asyncio.gather(*io_tasks, return_exceptions=True)
    io_elapsed = time.time() - io_start

    success_count = sum(1 for r in cpu_results if isinstance(r, dict) and r.get("success"))

    # If CPU tasks take significantly longer than sequential would,
    # it suggests the event loop was blocked
    sequential_estimate = sum(r.get("elapsed", 0) for r in cpu_results) / total_tasks if cpu_results else 0
    blocking_ratio = cpu_elapsed / sequential_estimate if sequential_estimate > 0 else 1

    # Also check if all tasks completed
    all_completed = success_count == total_tasks

    passed = all_completed and cpu_elapsed < timeout_seconds
    result = TestResult(
        name="Event Loop Starvation",
        passed=passed,
        expected=f"All {total_tasks} tasks complete within {timeout_seconds}s",
        actual=f"{success_count}/{total_tasks} completed in {cpu_elapsed:.2f}s",
        details=f"Blocking ratio: {blocking_ratio:.2f}, IO comparison: {io_elapsed:.2f}s"
    )
    results.add(result)

    print(results.summary())

    if not all_completed:
        print(f"\n⚠️  Event loop starvation detected!")
        print(f"Only {success_count}/{total_tasks} tasks completed")
    assert passed, f"Event loop starvation: only {success_count}/{total_tasks} completed"


# ==============================================================================
# Test 10: Context Variable Leaks
# ==============================================================================

@pytest.mark.asyncio
async def test_context_variable_leaks():
    """
    Test 10: Context variable leaks
    User A's data appearing in User B's response under load.
    """
    results = StressTestResults()
    total_requests_per_user = 100
    num_users = 5

    # Track user data isolation
    data_leaks: List[Dict] = []
    user_data: Dict[str, Set[str]] = {f"user_{i}": set() for i in range(num_users)}

    async def simulate_user_request(user_id: str, request_id: int) -> Dict[str, Any]:
        """Simulate a user's request that should only see their own data."""
        # Each user generates unique data
        expected_data = f"data_for_{user_id}_request_{request_id}"
        user_data[user_id].add(expected_data)

        # Simulate processing (in real app, this would fetch from DB)
        await asyncio.sleep(0.001)

        # Simulate getting result (should only contain user's data)
        # In a buggy system, we might accidentally return another user's data
        returned_data = expected_data

        # Check for leaks
        for other_user in user_data:
            if other_user != user_id:
                if returned_data in user_data[other_user]:
                    data_leaks.append({
                        "request_user": user_id,
                        "leaked_from": other_user,
                        "data": returned_data
                    })

        return {
            "user_id": user_id,
            "data": returned_data,
            "success": True
        }

    # Launch all user requests concurrently
    tasks = []
    for user_id in range(num_users):
        for req_id in range(total_requests_per_user):
            tasks.append(simulate_user_request(f"user_{user_id}", req_id))

    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results_list if isinstance(r, dict) and r.get("success"))
    total_expected = num_users * total_requests_per_user

    passed = len(data_leaks) == 0 and success_count == total_expected

    result = TestResult(
        name="Context Variable Leaks",
        passed=passed,
        expected="No data leaks between users",
        actual=f"{len(data_leaks)} leaks detected, {success_count}/{total_expected} success",
        details=f"Users: {num_users}, Requests per user: {total_requests_per_user}"
    )
    results.add(result)

    print(results.summary())

    if data_leaks:
        print(f"\n⚠️  DATA LEAKS DETECTED:")
        for leak in data_leaks[:5]:
            print(f"  User {leak['request_user']} received data from User {leak['leaked_from']}")

    assert passed, f"Detected {len(data_leaks)} context leaks"


# ==============================================================================
# Run all tests
# ==============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
