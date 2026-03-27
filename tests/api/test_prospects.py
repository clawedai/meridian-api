"""Tests for prospects API endpoints."""
import httpx
import pytest
from unittest.mock import AsyncMock


class TestListProspects:
    """Tests for GET /api/v1/prospects"""

    def test_list_prospects_returns_prospects(
        self, client, mock_httpx_async_client, mock_user_id
    ):
        """Should return list of prospects for authenticated user."""
        prospects = [
            {
                "id": "prospect_1",
                "user_id": mock_user_id,
                "full_name": "Alice Smith",
                "first_name": "Alice",
                "company": "Acme Corp",
                "title": "VP Engineering",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            {
                "id": "prospect_2",
                "user_id": mock_user_id,
                "full_name": "Bob Jones",
                "first_name": "Bob",
                "company": "Beta Inc",
                "title": "CTO",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
        ]
        prospects_resp = httpx.Response(200, json=prospects)
        signal_resp = httpx.Response(200, json=[{"score": 60, "tier": "hot", "score_breakdown": {}}])

        async def mock_get(url, **kwargs):
            if "intent_scores" in url:
                return signal_resp
            return prospects_resp

        mock_httpx_async_client.get = mock_get
        mock_httpx_async_client.patch = AsyncMock(return_value=httpx.Response(200))

        response = client.get("/api/v1/prospects")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_prospects_requires_auth(self, mock_httpx_async_client):
        """Unauthenticated request should return 401."""
        from fastapi.testclient import TestClient
        from main import app
        from app.api.deps import get_current_user, get_user_context

        app.dependency_overrides.clear()

        client = TestClient(app)
        response = client.get("/api/v1/prospects")
        assert response.status_code == 401

        # Restore overrides
        from tests.conftest import mock_user, mock_token
        app.dependency_overrides[get_current_user] = lambda: mock_user(mock_token("user_test_123"))
        app.dependency_overrides[get_user_context] = lambda: {"user_id": "user_test_123", "user_token": "token"}


class TestCreateProspect:
    """Tests for POST /api/v1/prospects"""

    def test_create_prospect_success(self, client, mock_httpx_async_client, mock_user_id):
        """Should create a new prospect and return it."""
        created = {
            "id": "new_prospect_id",
            "user_id": mock_user_id,
            "full_name": "Charlie Davis",
            "first_name": "Charlie",
            "company": "Gamma LLC",
            "title": "CEO",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        async def mock_post(url, **kwargs):
            return httpx.Response(201, json=[created])

        mock_httpx_async_client.post = mock_post

        response = client.post(
            "/api/v1/prospects",
            json={
                "full_name": "Charlie Davis",
                "first_name": "Charlie",
                "company": "Gamma LLC",
                "title": "CEO",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["full_name"] == "Charlie Davis"


class TestGetProspect:
    """Tests for GET /api/v1/prospects/{id}"""

    def test_get_prospect_returns_404_for_nonexistent(
        self, client, mock_httpx_async_client
    ):
        """Should return 404 for a prospect that doesn't exist."""
        async def mock_get(url, **kwargs):
            return httpx.Response(200, json=[])

        mock_httpx_async_client.get = mock_get

        response = client.get("/api/v1/prospects/nonexistent_id")
        assert response.status_code == 404
