"""
Stripe billing webhook handlers for Drishti intelligence platform.
Handles subscription lifecycle events from Stripe webhooks.
"""

import logging
from typing import Optional
from datetime import datetime, timedelta

import httpx
from stripe import Event, Subscription
from stripe.checkout import Session as CheckoutSession

from app.core.config import settings

logger = logging.getLogger(__name__)

# Plan/tier configuration based on price_id
PLAN_MAPPING = {
    "price_starter": {
        "tier": "starter",
        "name": "Starter",
        "entities_limit": 5,
        "sources_limit": 10,
    },
    "price_growth": {
        "tier": "growth",
        "name": "Growth",
        "entities_limit": 20,
        "sources_limit": 40,
    },
    "price_scale": {
        "tier": "scale",
        "name": "Scale",
        "entities_limit": -1,
        "sources_limit": -1,
    },
}

DEFAULT_PLAN = {
    "tier": "starter",
    "name": "Starter",
    "entities_limit": 5,
    "sources_limit": 10,
}


class BillingServiceError(Exception):
    def __init__(self, message: str, event_type: Optional[str] = None, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.event_type = event_type
        self.details = details or {}


class BillingService:
    def __init__(self):
        self.supabase_url = settings.SUPABASE_URL
        self.service_key = settings.SUPABASE_SERVICE_KEY
        self._headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._client = httpx.AsyncClient(base_url=self.supabase_url, headers=self._headers, timeout=30.0)

    def _get_plan_details(self, price_id: str) -> dict:
        return PLAN_MAPPING.get(price_id, DEFAULT_PLAN)

    def _get_supabase_error_message(self, response: httpx.Response) -> str:
        try:
            error_data = response.json()
            return error_data.get("message", error_data.get("error", {}).get("message", "Unknown error"))
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:200]}"

    async def _is_event_processed(self, event_id: str) -> bool:
        """Check if this Stripe event was already processed"""
        if not event_id:
            return False

        try:
            response = await self._client.get(
                "/rest/v1/processed_webhooks",
                params={"event_id": f"eq.{event_id}", "select": "id"},
            )
            if response.status_code == 200 and response.json():
                logger.info(f"Event {event_id} already processed, skipping")
                return True
            return False
        except httpx.HTTPError as e:
            logger.error(f"Error checking processed event: {e}")
            return False

    async def _mark_event_processed(self, event_id: str) -> bool:
        """Mark a Stripe event as processed"""
        if not event_id:
            return False

        try:
            response = await self._client.post(
                "/rest/v1/processed_webhooks",
                json={"event_id": event_id},
            )
            if response.status_code in (200, 201):
                logger.info(f"Marked event {event_id} as processed")
                return True
            elif response.status_code == 409:
                logger.info(f"Event {event_id} already marked as processed (race condition)")
                return True
            else:
                logger.warning(f"Failed to mark event {event_id} as processed: {response.status_code}")
                return False
        except httpx.HTTPError as e:
            logger.error(f"Error marking event as processed: {e}")
            return False

    async def activate_subscription(
        self,
        user_id: str,
        subscription_id: str,
        customer_id: str,
        price_id: str,
    ) -> dict:
        # Validate user_id is present
        if not user_id:
            logger.error("Cannot activate subscription: user_id is null or empty")
            raise BillingServiceError("user_id is required for subscription activation")

        plan_details = self._get_plan_details(price_id)
        current_period_start = datetime.utcnow()
        current_period_end = current_period_start + timedelta(days=30)

        subscription_data = {
            "user_id": user_id,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "price_id": price_id,
            "tier": plan_details["tier"],
            "plan_name": plan_details["name"],
            "entities_limit": plan_details["entities_limit"],
            "sources_limit": plan_details["sources_limit"],
            "status": "active",
            "current_period_start": current_period_start.isoformat(),
            "current_period_end": current_period_end.isoformat(),
            "cancel_at_period_end": False,
            "updated_at": datetime.utcnow().isoformat(),
        }

        logger.info(f"Activating subscription for user {user_id}: subscription_id={subscription_id}, tier={plan_details['tier']}")

        try:
            check_response = await self._client.get(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}", "select": "id"},
            )

            if check_response.status_code == 200 and check_response.json():
                update_payload = {k: v for k, v in subscription_data.items() if k not in ["user_id", "subscription_id"]}
                response = await self._client.patch(
                    "/rest/v1/user_subscriptions",
                    params={"subscription_id": f"eq.{subscription_id}"},
                    json=update_payload,
                )
                action = "updated"
            else:
                response = await self._client.post("/rest/v1/user_subscriptions", json=subscription_data)
                action = "created"

            if response.status_code in (200, 201):
                result = response.json()
                logger.info(f"Successfully {action} subscription {subscription_id} for user {user_id}")
                return result[0] if isinstance(result, list) else result
            else:
                error_msg = self._get_supabase_error_message(response)
                raise BillingServiceError(f"Failed to {action} subscription: {error_msg}")

        except httpx.HTTPError as e:
            logger.error(f"HTTP error activating subscription: {e}")
            raise BillingServiceError(f"Network error activating subscription: {str(e)}")

    async def cancel_subscription(self, subscription_id: str) -> dict:
        logger.info(f"Cancelling subscription: {subscription_id}")

        try:
            check_response = await self._client.get(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}", "select": "id,status"},
            )

            if check_response.status_code != 200 or not check_response.json():
                raise BillingServiceError(f"Subscription not found: {subscription_id}")

            update_payload = {
                "status": "cancelled",
                "cancel_at_period_end": True,
                "cancelled_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            response = await self._client.patch(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}"},
                json=update_payload,
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Successfully cancelled subscription {subscription_id}")
                return result[0] if isinstance(result, list) else result
            else:
                error_msg = self._get_supabase_error_message(response)
                raise BillingServiceError(f"Failed to cancel subscription: {error_msg}")

        except httpx.HTTPError as e:
            logger.error(f"HTTP error cancelling subscription: {e}")
            raise BillingServiceError(f"Network error cancelling subscription: {str(e)}")

    async def update_subscription(self, subscription_id: str, new_price_id: str) -> dict:
        plan_details = self._get_plan_details(new_price_id)
        logger.info(f"Updating subscription {subscription_id} to plan {plan_details['tier']}")

        try:
            check_response = await self._client.get(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}", "select": "id,tier,status"},
            )

            if check_response.status_code != 200 or not check_response.json():
                raise BillingServiceError(f"Subscription not found: {subscription_id}")

            existing = check_response.json()[0]
            update_payload = {
                "price_id": new_price_id,
                "tier": plan_details["tier"],
                "plan_name": plan_details["name"],
                "entities_limit": plan_details["entities_limit"],
                "sources_limit": plan_details["sources_limit"],
                "updated_at": datetime.utcnow().isoformat(),
            }

            response = await self._client.patch(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}"},
                json=update_payload,
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Successfully updated subscription {subscription_id}")
                return result[0] if isinstance(result, list) else result
            else:
                error_msg = self._get_supabase_error_message(response)
                raise BillingServiceError(f"Failed to update subscription: {error_msg}")

        except httpx.HTTPError as e:
            logger.error(f"HTTP error updating subscription: {e}")
            raise BillingServiceError(f"Network error updating subscription: {str(e)}")

    async def handle_webhook_event(self, event: "Event") -> dict:
        event_type = event.type
        event_id = event.id
        logger.info(f"Processing billing webhook event: {event_type}, id={event_id}")

        # IDEMPOTENCY CHECK: Skip if already processed
        if event_id and await self._is_event_processed(event_id):
            return {"status": "already_processed", "event_type": event_type, "event_id": event_id}

        handlers = {
            "customer.subscription.created": self._handle_subscription_created,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "checkout.session.completed": self._handle_checkout_completed,
            "invoice.payment_succeeded": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_invoice_failed,
        }

        handler = handlers.get(event_type)
        if not handler:
            return {"status": "ignored", "event_type": event_type}

        try:
            result = await handler(event.data.object)
            # Mark as processed after successful handling
            if event_id:
                await self._mark_event_processed(event_id)
            return {"status": "success", "event_type": event_type, "result": result}
        except Exception as e:
            logger.exception(f"Error handling {event_type}: {e}")
            return {"status": "error", "event_type": event_type, "error": str(e)}

    async def _handle_subscription_created(self, subscription: "Subscription") -> dict:
        user_id = subscription.metadata.get("user_id") if subscription.metadata else None
        price_id = subscription.items.data[0].price.id if subscription.items and subscription.items.data else ""

        if not user_id:
            logger.error(f"Subscription created event missing user_id: subscription_id={subscription.id}")
            raise BillingServiceError("Missing user_id in subscription metadata")

        return await self.activate_subscription(
            user_id=user_id,
            subscription_id=subscription.id,
            customer_id=subscription.customer,
            price_id=price_id,
        )

    async def _handle_subscription_updated(self, subscription: "Subscription") -> dict:
        price_id = subscription.items.data[0].price.id if subscription.items and subscription.items.data else None

        if subscription.status == "canceled" or subscription.cancel_at_period_end:
            return await self.cancel_subscription(subscription.id)
        elif price_id:
            return await self.update_subscription(subscription.id, price_id)

        response = await self._client.patch(
            "/rest/v1/user_subscriptions",
            params={"subscription_id": f"eq.{subscription.id}"},
            json={"status": subscription.status, "cancel_at_period_end": subscription.cancel_at_period_end},
        )
        return response.json()[0] if response.status_code == 200 else {}

    async def _handle_subscription_deleted(self, subscription: "Subscription") -> dict:
        return await self.cancel_subscription(subscription.id)

    async def _handle_checkout_completed(self, session: "Checkout.Session") -> dict:
        customer_id = session.customer
        subscription_id = session.subscription
        user_id = session.metadata.get("user_id") if session.metadata else None
        price_id = session.metadata.get("price_id") if session.metadata else None

        if not subscription_id or not user_id or not price_id:
            raise BillingServiceError("Missing required data in checkout session")

        return await self.activate_subscription(user_id, subscription_id, customer_id, price_id)

    async def _handle_invoice_paid(self, invoice: dict) -> dict:
        logger.info(f"Invoice paid: {invoice.get('id')}")
        return {"status": "processed", "invoice_id": invoice.get("id")}

    async def _handle_invoice_failed(self, invoice: dict) -> dict:
        subscription_id = invoice.get("subscription")
        logger.warning(f"Invoice payment failed for subscription {subscription_id}")

        if subscription_id:
            response = await self._client.patch(
                "/rest/v1/user_subscriptions",
                params={"subscription_id": f"eq.{subscription_id}"},
                json={"status": "past_due", "updated_at": datetime.utcnow().isoformat()},
            )
            return response.json()[0] if response.status_code == 200 else {}

        return {"status": "processed"}


billing_service = BillingService()
