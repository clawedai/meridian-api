from fastapi import APIRouter, Depends, HTTPException, Header, Query
from typing import List, Optional
import httpx
import logging
from datetime import datetime, timedelta
from ..deps import get_current_user, get_supabase, SupabaseClient, get_supabase_service_client

logger = logging.getLogger(__name__)
from ...schemas.entity import AlertCreate, AlertUpdate, AlertResponse
from ...services.notifier import EmailNotifier, WebhookNotifier

router = APIRouter(prefix="/alerts", tags=["Alerts"])


# ============================================================================
# Alert Condition Evaluation Functions
# ============================================================================

def _evaluate_keyword_condition(config: dict, text: str) -> bool:
    """Check if text contains keywords"""
    keywords = config.get("keywords", [])
    match_all = config.get("match_all", False)
    text_lower = text.lower()

    if not keywords:
        return False

    matches = [kw.lower() in text_lower for kw in keywords]

    if match_all:
        return all(matches)
    return any(matches)


def _evaluate_metric_condition(config: dict, insight: dict) -> bool:
    """Check if metric meets threshold"""
    metric = config.get("metric")
    operator = config.get("operator", "eq")
    value = config.get("value")

    if not metric or value is None:
        return False

    insight_value = insight.get(metric)

    # Normalize importance values
    importance_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    if metric == "importance":
        insight_val = importance_order.get(str(insight_value).lower(), 0)
        threshold_val = importance_order.get(str(value).lower(), 0)

        if operator == "gte":
            return insight_val >= threshold_val
        elif operator == "gt":
            return insight_val > threshold_val
        elif operator == "eq":
            return insight_val == threshold_val
    else:
        # Numeric comparison
        try:
            insight_val = float(insight_value)
            threshold_val = float(value)

            if operator == "gte":
                return insight_val >= threshold_val
            elif operator == "gt":
                return insight_val > threshold_val
            elif operator == "eq":
                return insight_val == threshold_val
            elif operator == "lte":
                return insight_val <= threshold_val
            elif operator == "lt":
                return insight_val < threshold_val
        except (TypeError, ValueError):
            return False

    return False


def _evaluate_pattern_condition(config: dict, insight: dict) -> bool:
    """Check for pattern matches (trends, spikes)"""
    pattern_type = config.get("pattern_type")

    if pattern_type == "any_critical":
        return insight.get("importance") == "critical"
    elif pattern_type == "any_high":
        return insight.get("importance") in ["high", "critical"]
    elif pattern_type == "high_confidence":
        return insight.get("confidence", 0) >= 0.8

    return False


def evaluate_condition(alert_rule: dict, insights: List[dict]) -> List[dict]:
    """
    Evaluate an alert rule against a list of insights.
    Returns list of matching insights.
    """
    condition_type = alert_rule.get("alert_condition_type")
    condition_config = alert_rule.get("condition_config", {})

    matching_insights = []

    for insight in insights:
        matched = False
        text = f"{insight.get('title', '')} {insight.get('content', '')}"

        if condition_type == "keyword_match":
            matched = _evaluate_keyword_condition(condition_config, text)

        elif condition_type == "threshold_breach":
            matched = _evaluate_metric_condition(condition_config, insight)

        elif condition_type == "trend_pattern":
            matched = _evaluate_pattern_condition(condition_config, insight)

        elif condition_type == "new_mention":
            # New mentions are all insights (they're new by definition)
            matched = True

        if matched:
            matching_insights.append(insight)

    return matching_insights


async def trigger_alerts_for_entity(
    entity_id: str,
    supabase_url: str,
    anon_key: str,
    user_id: str,
    hours_back: int = 24
) -> dict:
    """
    Evaluate all active alert rules for an entity and trigger matching ones.
    Returns summary of triggered alerts.
    """
    try:
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
        }

        # Get active alert rules for this entity (filtered by user_id for RLS)
        alerts_url = f"{supabase_url}/rest/v1/alerts"
        alerts_params = f"user_id=eq.{user_id}&entity_id=eq.{entity_id}&is_active=eq.true&select=*"

        async with httpx.AsyncClient(timeout=30.0) as client:
            alerts_response = await client.get(f"{alerts_url}?{alerts_params}", headers=headers)

        if alerts_response.status_code != 200:
            return {"error": "Failed to fetch alerts", "details": alerts_response.text}

        alert_rules = alerts_response.json()

        if not alert_rules:
            return {"alerts_checked": 0, "alerts_triggered": 0, "matches": []}

        # Get recent insights for this entity (user_id filter for RLS compliance)
        # Note: If insights table doesn't have direct user_id column, this filter
        # should be adjusted to filter through entity ownership instead
        cutoff_time = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
        insights_url = f"{supabase_url}/rest/v1/insights"
        insights_params = f"user_id=eq.{user_id}&entity_id=eq.{entity_id}&generated_at=gte.{cutoff_time}&is_archived=eq.false&select=*"

        async with httpx.AsyncClient(timeout=30.0) as client:
            insights_response = await client.get(f"{insights_url}?{insights_params}", headers=headers)

        if insights_response.status_code == 200:
            insights = insights_response.json()
        else:
            logger.warning(f"Failed to fetch insights for entity {entity_id}: {insights_response.status_code} - {insights_response.text}")
            insights = []

        # Evaluate each alert rule
        triggered_count = 0
        all_matches = []

        for rule in alert_rules:
            matches = evaluate_condition(rule, insights)

            if matches:
                triggered_count += 1
                all_matches.extend(matches)

                # Update trigger count
                update_url = f"{alerts_url}?id=eq.{rule['id']}"
                update_data = {
                    "last_triggered_at": datetime.utcnow().isoformat(),
                    "trigger_count": rule.get("trigger_count", 0) + 1,
                }

                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.patch(update_url, json=update_data, headers=headers)

        return {
            "alerts_checked": len(alert_rules),
            "alerts_triggered": triggered_count,
            "matches": all_matches[:20],  # Limit matches returned
        }

    except Exception as e:
        return {"error": str(e)}


def get_auth_header(authorization: str = Header(None)) -> str:
    """Extract token from Authorization header"""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization or ""


@router.get("", response_model=List[AlertResponse])
async def list_alerts(
    entity_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """List all alerts for current user"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        params = [
            f"user_id=eq.{user_id}",
            f"order=created_at.desc",
            f"limit={limit}",
            f"offset={skip}",
        ]
        if entity_id:
            params.append(f"entity_id=eq.{entity_id}")
        if is_active is not None:
            params.append(f"is_active=eq.{str(is_active).lower()}")

        url = f"{supabase.url}/rest/v1/alerts"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            alerts = response.json()
            return [AlertResponse(**a) for a in alerts]
        logger.warning(f"list_alerts: Non-200 response status={response.status_code}")
        return []
    except Exception as e:
        logger.error(f"list_alerts: Exception fetching alerts: {str(e)}")
        return []


@router.post("", response_model=AlertResponse)
async def create_alert(
    alert: AlertCreate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Create a new alert"""
    try:
        alert_type = alert.alert_condition_type.value if hasattr(alert.alert_condition_type, 'value') else alert.alert_condition_type
        channels = [c.value if hasattr(c, 'value') else c for c in alert.channels]

        data = {
            "user_id": current_user["id"],
            "entity_id": alert.entity_id,
            "name": alert.name,
            "description": alert.description,
            "alert_condition_type": alert_type,
            "condition_config": alert.condition_config or {},
            "channels": channels,
            "webhook_url": alert.webhook_url,
            "email_frequency": alert.email_frequency,
            "is_active": True,
        }

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = f"{supabase.url}/rest/v1/alerts"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)

        if response.status_code in [200, 201]:
            result = response.json()
            return AlertResponse(**result[0])

        raise HTTPException(status_code=400, detail=f"Failed to create alert: {response.text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get alert by ID"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        url = supabase.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return AlertResponse(**data[0])

        raise HTTPException(status_code=404, detail="Alert not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: str,
    alert: AlertUpdate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Update an alert"""
    try:
        update_data = {}
        if alert.name is not None:
            update_data["name"] = alert.name
        if alert.description is not None:
            update_data["description"] = alert.description
        if alert.condition_config is not None:
            update_data["condition_config"] = alert.condition_config
        if alert.channels is not None:
            update_data["channels"] = [c.value if hasattr(c, 'value') else c for c in alert.channels]
        if alert.webhook_url is not None:
            update_data["webhook_url"] = alert.webhook_url
        if alert.email_frequency is not None:
            update_data["email_frequency"] = alert.email_frequency
        if alert.is_active is not None:
            update_data["is_active"] = alert.is_active

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = supabase.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=update_data, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return AlertResponse(**data[0])

        raise HTTPException(status_code=404, detail="Alert not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Delete an alert"""
    try:
        headers = supabase._get_headers()
        headers["Prefer"] = "return=minimal"

        url = supabase.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=headers)

        if response.status_code in [200, 204]:
            return {"message": "Alert deleted successfully"}
        raise HTTPException(status_code=404, detail="Alert not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{alert_id}/test")
async def test_alert(
    alert_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Test alert - sends a test notification"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        # Get alert details
        url = supabase.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Alert not found")

        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Alert not found")

        alert = data[0]
        channels = alert.get("channels", [])

        # Get user email
        profile_url = f"{supabase.url}/rest/v1/profiles"
        profile_params = [f"id=eq.{user_id}"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            profile_response = await client.get(profile_url, headers=headers, params=profile_params)

        user_email = current_user.get("email")
        if profile_response.status_code == 200:
            profile_data = profile_response.json()
            if profile_data:
                user_email = profile_data[0].get("email", user_email)

        # Create sample insight for test
        sample_insight = {
            "title": f"Test Alert: {alert.get('name', 'Untitled')}",
            "content": "This is a test notification to verify your alert configuration is working correctly.",
            "importance": "medium",
            "confidence": 0.95,
        }

        results = {
            "email": None,
            "webhook": None,
        }

        # Send email notification
        if "email" in channels and user_email:
            email_notifier = EmailNotifier()
            email_result = await email_notifier.send_alert(
                to=user_email,
                insight=sample_insight,
                entity_name="Test Entity"
            )
            results["email"] = email_result

        # Send webhook notification
        webhook_url = alert.get("webhook_url")
        if "webhook" in channels and webhook_url:
            webhook_notifier = WebhookNotifier()
            webhook_result = await webhook_notifier.send(
                webhook_url=webhook_url,
                payload={
                    "alert_name": alert.get("name"),
                    "insight": sample_insight,
                    "triggered_at": datetime.utcnow().isoformat(),
                    "is_test": True,
                }
            )
            results["webhook"] = webhook_result

        return {
            "message": "Test alert sent",
            "alert_name": alert.get("name"),
            "channels": channels,
            "results": results,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{alert_id}/trigger")
async def trigger_alert(
    alert_id: str,
    insight_data: dict = None,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Trigger an alert with actual insight data"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        # Get alert details
        url = supabase.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200 or not response.json():
            raise HTTPException(status_code=404, detail="Alert not found")

        alert = response.json()[0]
        channels = alert.get("channels", [])

        if insight_data is None:
            insight_data = {
                "title": f"Alert Triggered: {alert.get('name', 'Untitled')}",
                "content": "Your alert condition was met.",
                "importance": alert.get("alert_condition_type", "medium"),
                "confidence": 0.8,
            }

        # Get user email
        profile_url = f"{supabase.url}/rest/v1/profiles"
        profile_params = [f"id=eq.{user_id}"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            profile_response = await client.get(profile_url, headers=headers, params=profile_params)

        user_email = current_user.get("email")
        if profile_response.status_code == 200:
            profile_data = profile_response.json()
            if profile_data:
                user_email = profile_data[0].get("email", user_email)

        # Get entity name if available
        entity_name = "Unknown Entity"
        entity_id = alert.get("entity_id")
        if entity_id:
            entity_url = f"{supabase.url}/rest/v1/entities"
            entity_params = [f"id=eq.{entity_id}"]
            async with httpx.AsyncClient(timeout=30.0) as client:
                entity_response = await client.get(entity_url, headers=headers, params=entity_params)
            if entity_response.status_code == 200:
                entity_data = entity_response.json()
                if entity_data:
                    entity_name = entity_data[0].get("name", entity_name)

        results = {}

        # Send email notification
        if "email" in channels and user_email:
            email_notifier = EmailNotifier()
            results["email"] = await email_notifier.send_alert(
                to=user_email,
                insight=insight_data,
                entity_name=entity_name
            )

        # Send webhook notification
        webhook_url = alert.get("webhook_url")
        if "webhook" in channels and webhook_url:
            webhook_notifier = WebhookNotifier()
            results["webhook"] = await webhook_notifier.send(
                webhook_url=webhook_url,
                payload={
                    "alert_id": alert_id,
                    "alert_name": alert.get("name"),
                    "insight": insight_data,
                    "entity_name": entity_name,
                    "triggered_at": datetime.utcnow().isoformat(),
                }
            )

        # Update alert last_triggered_at and trigger_count
        update_url = f"{supabase.url}/rest/v1/alerts"
        update_params = [f"id=eq.{alert_id}"]
        update_data = {
            "last_triggered_at": datetime.utcnow().isoformat(),
            "trigger_count": alert.get("trigger_count", 0) + 1,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.patch(update_url, json=update_data, headers=headers, params=update_params)

        return {
            "message": "Alert triggered successfully",
            "alert_name": alert.get("name"),
            "results": results,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Alert Evaluation Endpoints
# ============================================================================

@router.post("/evaluate/{entity_id}")
async def evaluate_entity_alerts(
    entity_id: str,
    hours_back: int = 24,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Evaluate all active alerts for an entity.
    Returns list of triggered alerts and matching insights.
    """
    try:
        result = await trigger_alerts_for_entity(
            entity_id=entity_id,
            supabase_url=supabase.url,
            anon_key=supabase.anon_key,
            user_id=current_user["id"],
            hours_back=hours_back
        )

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check-insight")
async def check_insight_against_alerts(
    entity_id: str,
    insight: dict,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Check a single insight against all active alerts for an entity.
    Useful for evaluating insights as they are generated.
    """
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        # Get active alert rules for this entity (filtered by user_id for RLS)
        url = f"{supabase.url}/rest/v1/alerts"
        params = f"user_id=eq.{user_id}&entity_id=eq.{entity_id}&is_active=eq.true&select=*"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{url}?{params}", headers=headers)

        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch alerts")

        alert_rules = response.json()

        # Evaluate against each rule
        results = []
        for rule in alert_rules:
            matches = evaluate_condition(rule, [insight])
            if matches:
                results.append({
                    "alert_id": rule["id"],
                    "alert_name": rule["name"],
                    "matched": True,
                    "condition_type": rule["alert_condition_type"],
                })

        return {
            "entity_id": entity_id,
            "insight_title": insight.get("title"),
            "alerts_checked": len(alert_rules),
            "alerts_triggered": len(results),
            "triggered_alerts": results,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Notification Alert Endpoints (prospect-level score_spike / tier_change)
# ============================================================================

@router.get("/notification-alerts")
async def list_notification_alerts(
    limit: int = Query(20, ge=1, le=100),
    alert_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """List recent notification alerts for the current user (score_spike, tier_change)."""
    try:
        user_id = current_user["id"]
        client = get_supabase_service_client()

        params = [
            f"user_id=eq.{user_id}",
            "order=created_at.desc",
            f"limit={limit}",
        ]
        if alert_type:
            params.append(f"type=eq.{alert_type}")

        headers = client._get_admin_headers()
        headers["Prefer"] = "return=representation"

        url = client.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.get(url, headers=headers)

        if response.status_code == 200:
            return response.json()
        logger.warning(f"list_notification_alerts: {response.status_code} - {response.text}")
        return []
    except Exception as e:
        logger.error(f"list_notification_alerts: {e}")
        return []


@router.get("/notification-alerts/count")
async def get_notification_alert_count(current_user: dict = Depends(get_current_user)):
    """Get count of unread notification alerts."""
    try:
        user_id = current_user["id"]
        client = get_supabase_service_client()

        headers = client._get_admin_headers()
        headers["Prefer"] = "return=representation"

        params = [
            f"user_id=eq.{user_id}",
            "read=eq.false",
            "select=id",
        ]
        url = client.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            return {"count": len(data)}
        return {"count": 0}
    except Exception as e:
        logger.error(f"get_notification_alert_count: {e}")
        return {"count": 0}


@router.post("/notification-alerts/{alert_id}/read")
async def mark_notification_alert_read(
    alert_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a single notification alert as read."""
    try:
        user_id = current_user["id"]
        client = get_supabase_service_client()

        headers = client._get_admin_headers()
        headers["Prefer"] = "return=minimal"

        params = [f"id=eq.{alert_id}", f"user_id=eq.{user_id}"]
        url = client.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.patch(url, json={"read": True}, headers=headers)

        if response.status_code not in (200, 204):
            logger.warning(f"mark_notification_alert_read: {response.status_code} - {response.text}")

        return {"success": True}
    except Exception as e:
        logger.error(f"mark_notification_alert_read: {e}")
        return {"success": True}


@router.post("/notification-alerts/read-all")
async def mark_all_notification_alerts_read(current_user: dict = Depends(get_current_user)):
    """Mark all notification alerts as read for the current user."""
    try:
        user_id = current_user["id"]
        client = get_supabase_service_client()

        headers = client._get_admin_headers()
        headers["Prefer"] = "return=minimal"

        params = [f"user_id=eq.{user_id}", "read=eq.false"]
        url = client.build_url("alerts", params)

        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.patch(url, json={"read": True}, headers=headers)

        if response.status_code not in (200, 204):
            logger.warning(f"mark_all_notification_alerts_read: {response.status_code} - {response.text}")

        return {"success": True}
    except Exception as e:
        logger.error(f"mark_all_notification_alerts_read: {e}")
        return {"success": True}


# ============================================================================
# Alert Engine — Manual Trigger
# ============================================================================

@router.post("/alert-engine/trigger")
async def trigger_alert_engine(
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger one Alert Engine cycle.
    Runs score-change detection across all prospects immediately.
    Returns the cycle summary (prospects checked, alerts fired, emails sent).
    """
    try:
        from app.services.alert_engine import AlertEngine
        engine = AlertEngine()
        summary = await engine.run_cycle()
        return {
            "message": "Alert engine cycle complete",
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"alert_engine/trigger: {e}")
        raise HTTPException(status_code=500, detail=str(e))
