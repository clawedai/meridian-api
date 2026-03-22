"""Tests for tier limits service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.tier_limits import TierService, TIER_LIMITS, require_entity_limit


class TestTierLimits:
    """Tier limits service tests."""

    @pytest.fixture
    def tier_service(self):
        return TierService(user_id="user_123", user_token="test_token")

    def test_tier_limits_config(self):
        """Test tier limits are properly configured."""
        assert "starter" in TIER_LIMITS
        assert "growth" in TIER_LIMITS
        assert "scale" in TIER_LIMITS
        assert None in TIER_LIMITS

        # Verify limits
        assert TIER_LIMITS["starter"]["entities"] == 5
        assert TIER_LIMITS["growth"]["entities"] == 20
        assert TIER_LIMITS["scale"]["entities"] == float("inf")

    @pytest.mark.asyncio
    async def test_get_user_tier_no_subscription(self, tier_service):
        """Test getting tier when user has no subscription."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(return_value=mock_response)

        tier = await tier_service.get_user_tier()
        assert tier is None

    @pytest.mark.asyncio
    async def test_get_user_tier_with_subscription(self, tier_service):
        """Test getting tier when user has subscription."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"tier": "growth"}]

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(return_value=mock_response)

        tier = await tier_service.get_user_tier()
        assert tier == "growth"

    @pytest.mark.asyncio
    async def test_check_entity_limit_within_limit(self, tier_service):
        """Test entity limit check when within limit."""
        # Mock get_user_tier
        mock_tier_response = MagicMock()
        mock_tier_response.status_code = 200
        mock_tier_response.json.return_value = [{"tier": "starter"}]

        # Mock resource count
        mock_count_response = MagicMock()
        mock_count_response.status_code = 200
        mock_count_response.json.return_value = [{"id": "1"}, {"id": "2"}]
        mock_count_response.headers = {"content-range": "0-1/2"}

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(
            side_effect=[mock_tier_response, mock_count_response]
        )

        allowed, current, limit = await tier_service.check_entity_limit()
        assert allowed is True
        assert current == 2
        assert limit == 5

    @pytest.mark.asyncio
    async def test_check_entity_limit_exceeded(self, tier_service):
        """Test entity limit check when limit exceeded."""
        # Mock get_user_tier
        mock_tier_response = MagicMock()
        mock_tier_response.status_code = 200
        mock_tier_response.json.return_value = [{"tier": "starter"}]

        # Mock resource count (at limit)
        mock_count_response = MagicMock()
        mock_count_response.status_code = 200
        mock_count_response.json.return_value = [{"id": str(i)} for i in range(5)]
        mock_count_response.headers = {"content-range": "0-4/5"}

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(
            side_effect=[mock_tier_response, mock_count_response]
        )

        allowed, current, limit = await tier_service.check_entity_limit()
        assert allowed is False
        assert current == 5
        assert limit == 5

    @pytest.mark.asyncio
    async def test_scale_tier_unlimited(self, tier_service):
        """Test scale tier returns unlimited."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"tier": "scale"}]

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(return_value=mock_response)

        allowed, current, limit = await tier_service.check_entity_limit()
        assert allowed is True
        assert limit == float("inf")

    @pytest.mark.asyncio
    async def test_enforce_entity_limit_raises(self, tier_service):
        """Test enforce raises HTTPException when exceeded."""
        from fastapi import HTTPException

        # Mock exceeded limit
        mock_tier_response = MagicMock()
        mock_tier_response.status_code = 200
        mock_tier_response.json.return_value = [{"tier": "starter"}]

        mock_count_response = MagicMock()
        mock_count_response.status_code = 200
        mock_count_response.json.return_value = [{"id": str(i)} for i in range(10)]
        mock_count_response.headers = {"content-range": "0-9/10"}

        tier_service._client = AsyncMock()
        tier_service._client.get = AsyncMock(
            side_effect=[mock_tier_response, mock_count_response]
        )

        with pytest.raises(HTTPException) as exc_info:
            await tier_service.enforce_entity_limit()
        assert exc_info.value.status_code == 403
