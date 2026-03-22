"""Tests for billing service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.billing_handlers import BillingService, BillingServiceError


class TestBillingService:
    """Billing service tests."""

    @pytest.fixture
    def billing_service(self):
        return BillingService()

    @pytest.mark.asyncio
    async def test_is_event_processed_new_event(self, billing_service):
        """Test checking new event that hasn't been processed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        billing_service._client = AsyncMock()
        billing_service._client.get = AsyncMock(return_value=mock_response)

        result = await billing_service._is_event_processed("evt_new_123")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_event_processed_existing_event(self, billing_service):
        """Test checking event that was already processed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "record_123"}]

        billing_service._client = AsyncMock()
        billing_service._client.get = AsyncMock(return_value=mock_response)

        result = await billing_service._is_event_processed("evt_existing_123")
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_event_processed_success(self, billing_service):
        """Test marking event as processed."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = [{"id": "record_123"}]

        billing_service._client = AsyncMock()
        billing_service._client.post = AsyncMock(return_value=mock_response)

        result = await billing_service._mark_event_processed("evt_123")
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_event_processed_duplicate(self, billing_service):
        """Test marking event when already exists (race condition)."""
        mock_response = MagicMock()
        mock_response.status_code = 409  # Conflict

        billing_service._client = AsyncMock()
        billing_service._client.post = AsyncMock(return_value=mock_response)

        result = await billing_service._mark_event_processed("evt_duplicate")
        assert result is True  # Should still return True

    @pytest.mark.asyncio
    async def test_activate_subscription_new(self, billing_service):
        """Test activating new subscription."""
        # Mock not exists check
        mock_check = MagicMock()
        mock_check.status_code = 200
        mock_check.json.return_value = []

        # Mock insert
        mock_insert = MagicMock()
        mock_insert.status_code = 201
        mock_insert.json.return_value = [{
            "id": "sub_new",
            "tier": "growth"
        }]

        billing_service._client = AsyncMock()
        billing_service._client.get = AsyncMock(return_value=mock_check)
        billing_service._client.post = AsyncMock(return_value=mock_insert)

        result = await billing_service.activate_subscription(
            user_id="user_123",
            subscription_id="sub_stripe_123",
            customer_id="cus_123",
            price_id="price_growth"
        )

        assert result["tier"] == "growth"

    def test_get_plan_details_mapping(self, billing_service):
        """Test price_id to plan mapping."""
        starter = billing_service._get_plan_details("price_starter")
        assert starter["tier"] == "starter"
        assert starter["entities_limit"] == 5

        growth = billing_service._get_plan_details("price_growth")
        assert growth["tier"] == "growth"
        assert growth["entities_limit"] == 20

        scale = billing_service._get_plan_details("price_scale")
        assert scale["tier"] == "scale"
        assert scale["entities_limit"] == -1  # Unlimited

        unknown = billing_service._get_plan_details("price_unknown")
        assert unknown["tier"] == "starter"  # Defaults to starter

    @pytest.mark.asyncio
    async def test_handle_webhook_event_idempotency(self, billing_service):
        """Test webhook event processing is idempotent."""
        mock_event = MagicMock()
        mock_event.id = "evt_existing"
        mock_event.type = "checkout.session.completed"

        # Mock event already processed
        mock_check = MagicMock()
        mock_check.status_code = 200
        mock_check.json.return_value = [{"id": "processed_record"}]

        billing_service._client = AsyncMock()
        billing_service._client.get = AsyncMock(return_value=mock_check)

        result = await billing_service.handle_webhook_event(mock_event)
        assert result["status"] == "already_processed"

    @pytest.mark.asyncio
    async def test_handle_webhook_event_unknown_type(self, billing_service):
        """Test handling unknown webhook event type."""
        mock_event = MagicMock()
        mock_event.id = "evt_new"
        mock_event.type = "unknown.event.type"

        # Mock event not processed
        mock_check = MagicMock()
        mock_check.status_code = 200
        mock_check.json.return_value = []

        billing_service._client = AsyncMock()
        billing_service._client.get = AsyncMock(return_value=mock_check)

        result = await billing_service.handle_webhook_event(mock_event)
        assert result["status"] == "ignored"
