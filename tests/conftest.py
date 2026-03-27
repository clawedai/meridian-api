"""Test fixtures and configuration."""
import asyncio
from datetime import datetime, timedelta
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from main import app
from app.api.deps import get_current_user, get_user_context


@pytest.fixture
def mock_user_id() -> str:
    return "user_test_123"


@pytest.fixture
def mock_user(mock_user_id: str) -> dict[str, Any]:
    return {
        "id": mock_user_id,
        "email": "test@example.com",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_token(mock_user_id: str) -> str:
    """Create a proper JWT token using the app's secret key."""
    from app.core.security import create_access_token
    return create_access_token({"sub": mock_user_id})


@pytest.fixture
def auth_headers(mock_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mock_token}"}


@pytest.fixture
def mock_httpx_async_client():
    """Create a mock httpx.AsyncClient that returns controlled responses."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.fixture
def client(mock_user: dict[str, Any], mock_httpx_async_client) -> Generator[TestClient, None, None]:
    """Create a test client with authentication mocked.

    Patches httpx.AsyncClient in each API module's namespace so the mock
    is used during actual test execution (not just during construction).
    """
    async def override_get_current_user():
        return mock_user

    async def override_get_user_context():
        return {
            "user_id": mock_user["id"],
            "user_token": "mock_token",
        }

    # Apply dependency overrides
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_user_context] = override_get_user_context

    # Patch httpx.AsyncClient in each module that imports it
    # This must be done at the module namespace level so the patch
    # survives through the entire test execution
    patches = [
        patch("app.api.v1.entities.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.sources.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.alerts.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.dashboard.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.insights.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.reports.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.me.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.competitive_groups.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.auth.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.prospects.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.api.v1.linkedin.httpx.AsyncClient", return_value=mock_httpx_async_client),
        patch("app.services.score_service.httpx.AsyncClient", return_value=mock_httpx_async_client),
    ]

    for p in patches:
        p.start()

    try:
        headers = {"Authorization": "Bearer mock_token"}
        with TestClient(app, headers=headers) as test_client:
            yield test_client
    finally:
        for p in patches:
            p.stop()
        app.dependency_overrides.clear()


@pytest.fixture
def mock_supabase_response():
    def _mock_response(data: list | dict, status_code: int = 200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = data
        mock.ok = status_code < 400
        return mock
    return _mock_response


@pytest.fixture
def mock_entity(mock_user_id: str) -> dict[str, Any]:
    return {
        "id": "entity_test_123",
        "user_id": mock_user_id,
        "name": "Test Corp",
        "type": "competitor",
        "website": "https://testcorp.com",
        "industry": "Technology",
        "description": "Test company",
        "tags": ["tech"],
        "is_archived": False,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_source(mock_user_id: str) -> dict[str, Any]:
    return {
        "id": "source_test_123",
        "user_id": mock_user_id,
        "entity_id": "entity_test_123",
        "name": "Test RSS Feed",
        "source_type": "rss",
        "url": "https://testcorp.com/feed.xml",
        "status": "active",
        "is_active": True,
        "fetch_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_subscription(mock_user_id: str) -> dict[str, Any]:
    return {
        "id": "sub_test_123",
        "user_id": mock_user_id,
        "subscription_id": "sub_stripe_123",
        "tier": "growth",
        "plan_name": "Growth",
        "status": "active",
        "entities_limit": 20,
        "sources_limit": 40,
        "current_period_start": "2024-01-01T00:00:00Z",
        "current_period_end": "2024-02-01T00:00:00Z",
    }
