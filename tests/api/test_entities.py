"""Tests for entity endpoints."""
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient


class TestEntities:
    """Entity endpoint tests."""

    def test_list_entities_unauthorized(self):
        """Test listing entities without auth - need a fresh client without overrides."""
        from main import app
        from app.api.deps import get_current_user, get_user_context
        # Temporarily clear overrides
        app.dependency_overrides.clear()
        client = TestClient(app)
        response = client.get("/api/v1/entities")
        assert response.status_code == 401
        # Restore the conftest overrides for other tests
        from tests.conftest import mock_user
        app.dependency_overrides[get_current_user] = lambda: mock_user("user_test_123")
        app.dependency_overrides[get_user_context] = lambda: {"user_id": "user_test_123", "user_token": "token"}

    def test_list_entities_with_auth(self, client: TestClient, mock_httpx_async_client, mock_entity: dict):
        """Test listing entities with auth."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_entity]
        mock_httpx_async_client.get = AsyncMock(return_value=mock_response)

        response = client.get("/api/v1/entities")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_create_entity_success(self, client: TestClient, mock_httpx_async_client):
        """Test creating an entity."""
        from fastapi import HTTPException

        with patch("app.services.tier_limits.TierService") as mock_tier:
            instance = AsyncMock()
            instance.enforce_entity_limit = AsyncMock(return_value=True)
            instance.close = AsyncMock()
            mock_tier.return_value = instance

            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = [{
                "id": "entity_new",
                "user_id": "user_test_123",
                "name": "New Corp",
                "type": "competitor",
                "website": "https://newcorp.com",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }]
            mock_httpx_async_client.post = AsyncMock(return_value=mock_response)

            response = client.post("/api/v1/entities", json={
                "name": "New Corp",
                "type": "competitor",
                "website": "https://newcorp.com"
            })

            # The endpoint returns 200 when the status is in [200, 201]
            assert response.status_code in [200, 201]

    def test_create_entity_tier_limit_exceeded(self, client: TestClient):
        """Test creating entity when tier limit is exceeded."""
        from fastapi import HTTPException

        with patch("app.services.tier_limits.TierService") as mock_tier:
            instance = AsyncMock()
            instance.enforce_entity_limit = AsyncMock(side_effect=HTTPException(status_code=403, detail="Limit exceeded"))
            instance.close = AsyncMock()
            mock_tier.return_value = instance

            response = client.post("/api/v1/entities", json={
                "name": "New Corp",
                "type": "competitor",
            })

            assert response.status_code == 403

    def test_get_entity_not_found(self, client: TestClient, mock_httpx_async_client):
        """Test getting non-existent entity."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_httpx_async_client.get = AsyncMock(return_value=mock_response)

        response = client.get("/api/v1/entities/nonexistent")
        assert response.status_code == 404

    def test_update_entity(self, client: TestClient, mock_httpx_async_client, mock_entity: dict):
        """Test updating an entity."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{**mock_entity, "name": "Updated Corp"}]
        mock_httpx_async_client.patch = AsyncMock(return_value=mock_response)

        response = client.patch(
            f"/api/v1/entities/{mock_entity['id']}",
            json={"name": "Updated Corp"}
        )

        assert response.status_code == 200

    def test_delete_entity(self, client: TestClient, mock_httpx_async_client, mock_entity: dict):
        """Test deleting an entity."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx_async_client.patch = AsyncMock(return_value=mock_response)

        response = client.delete(f"/api/v1/entities/{mock_entity['id']}")
        assert response.status_code == 200
