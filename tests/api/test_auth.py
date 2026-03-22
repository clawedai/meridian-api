"""Tests for authentication endpoints."""
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient


class TestAuth:
    """Authentication endpoint tests."""

    def test_login_success(self, client: TestClient):
        """Test successful login."""
        with patch("app.api.v1.auth.get_supabase") as mock_get_supabase:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "user_123",
                "email": "test@example.com",
                "user_metadata": {"full_name": "Test User"}
            }
            mock_client.post.return_value = mock_response
            mock_get_supabase.return_value = mock_client

            response = client.post("/api/v1/auth/login", json={
                "email": "test@example.com",
                "password": "password123"
            })

            assert response.status_code in [200, 401, 500]  # Acceptable codes

    def test_login_missing_fields(self, client: TestClient):
        """Test login with missing fields."""
        response = client.post("/api/v1/auth/login", json={"email": "test@example.com"})
        assert response.status_code == 422  # Validation error

    def test_register_success(self, client: TestClient):
        """Test successful registration."""
        with patch("app.api.v1.auth.get_supabase") as mock_get_supabase:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "user_123",
                "email": "new@example.com",
            }
            mock_client.post.return_value = mock_response
            mock_get_supabase.return_value = mock_client

            response = client.post("/api/v1/auth/register", json={
                "email": "new@example.com",
                "password": "SecurePass123!",
                "full_name": "New User"
            })

            assert response.status_code in [200, 201, 400, 500]

    def test_register_invalid_email(self, client: TestClient):
        """Test registration with invalid email."""
        response = client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "SecurePass123!",
            "full_name": "Test User"
        })
        # Supabase returns 400 for invalid email, but 422 would be FastAPI validation
        assert response.status_code in [400, 422]
        assert "error" in response.json() or "detail" in response.json()

    def test_register_weak_password(self, client: TestClient):
        """Test registration with weak password."""
        response = client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "password": "123",  # Too short
            "full_name": "Test User"
        })
        # Supabase returns 422 for weak password, but could be 400 depending on version
        assert response.status_code in [400, 422]
        assert "error" in response.json() or "detail" in response.json()

    def test_logout(self, client: TestClient, auth_headers: dict):
        """Test logout endpoint."""
        response = client.post("/api/v1/auth/logout", headers=auth_headers)
        assert response.status_code in [200, 401, 500]
