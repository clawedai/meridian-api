from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
import httpx
import stripe
from stripe import Webhook
from ..deps import get_current_user, SupabaseClient, get_supabase
from ...core.config import settings
from ...services.billing_handlers import billing_service

router = APIRouter(prefix="/billing", tags=["Billing"])

STRIPE_API = "https://api.stripe.com/v1"

class CreateCheckoutRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str

@router.post("/create-checkout")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Create a Stripe checkout session"""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="Stripe not configured"
        )

    try:
        url = f"{STRIPE_API}/checkout/sessions"
        headers = {
            "Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # Stripe requires form-encoded data
        data = {
            "mode": "subscription",
            "payment_method_types[0]": "card",
            "line_items[0][price]": request.price_id,
            "line_items[0][quantity]": "1",
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "metadata[user_id]": current_user["id"],
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, data=data, headers=headers)
            response.raise_for_status()

        return response.json()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks with signature verification"""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook secret not configured"
        )

    # Get raw body bytes (must be before any parsing)
    payload = await request.body()

    # Get signature header
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    # Verify signature and parse event
    try:
        event = Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        # Invalid payload structure
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        # Signature verification failed
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Now safely process the verified event using BillingService
    result = await billing_service.handle_webhook_event(event)
    return {"received": True, "result": result}

@router.get("/plans")
async def get_plans():
    """Get available subscription plans"""
    return [
        {
            "id": "price_starter",
            "name": "Starter",
            "price": 25000,
            "interval": "month",
            "features": [
                "5 entities",
                "10 data sources",
                "Weekly digest",
                "Email support",
            ],
        },
        {
            "id": "price_growth",
            "name": "Growth",
            "price": 50000,
            "interval": "month",
            "features": [
                "20 entities",
                "40 data sources",
                "Real-time alerts",
                "API access",
                "Custom scrapers",
            ],
            "popular": True,
        },
        {
            "id": "price_scale",
            "name": "Scale",
            "price": 75000,
            "interval": "month",
            "features": [
                "Unlimited entities",
                "Unlimited sources",
                "White-label reports",
                "5 team seats",
                "Priority support",
            ],
        },
    ]
