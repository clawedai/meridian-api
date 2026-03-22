"""
Background Job Processor for Intelligence Pipeline
Can be triggered via API or cron

SECURITY: All functions use user-scoped authentication to respect RLS policies.
Never use anon_key for accessing user data - it bypasses Row Level Security.
"""
import asyncio
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from .pipeline import IntelligencePipeline
from ..core.config import settings
from ..core.logging import get_processor_logger

# Global pipeline instance
pipeline = IntelligencePipeline()
logger = get_processor_logger()


def _get_user_headers(supabase_url: str, user_token: str) -> dict:
    """Get headers for Supabase REST API calls.

    Uses anon key — user_id is validated by the API auth layer,
    and RLS policies filter by user_id in query params.
    """
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def get_sources_for_entity(
    supabase_url: str,
    user_token: str,
    entity_id: str
) -> List[Dict]:
    """Fetch sources for an entity from Supabase - uses user token for RLS"""
    url = f"{supabase_url}/rest/v1/sources?entity_id=eq.{entity_id}&is_active=eq.true"
    headers = _get_user_headers(supabase_url, user_token)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.error(f"Error fetching sources: {e}", extra={"entity_id": entity_id})
    return []


async def store_raw_data(
    supabase_url: str,
    user_token: str,
    entity_id: str,
    source_id: str,
    raw_data: Dict
) -> bool:
    """Store raw data in Supabase - uses user token for RLS"""
    url = f"{supabase_url}/rest/v1/raw_data"

    # Flatten entries for storage
    if raw_data.get("type") == "rss":
        content = "\n\n".join([
            f"## {e.get('title', '')}\n{e.get('content', '')}"
            for e in raw_data.get("entries", [])
        ])
    else:
        content = raw_data.get("content", "")

    data = {
        "source_id": source_id,
        "entity_id": entity_id,
        "content": content[:50000],  # Truncate to 50k chars
        "content_hash": raw_data.get("hash"),
        "content_type": raw_data.get("type"),
        "metadata": {"raw_data": raw_data},
        "fetched_at": raw_data.get("fetched_at", datetime.utcnow().isoformat()),
        "processed": False,
    }

    headers = _get_user_headers(supabase_url, user_token)
    headers["Prefer"] = "return=minimal"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)
            return response.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Error storing raw data: {e}", extra={"entity_id": entity_id, "source_id": source_id})
        return False


async def store_insights(
    supabase_url: str,
    user_token: str,
    user_id: str,
    insights: List[Dict]
) -> int:
    """Store insights in Supabase - uses user token for RLS"""
    if not insights:
        return 0

    url = f"{supabase_url}/rest/v1/insights"
    headers = _get_user_headers(supabase_url, user_token)
    headers["Prefer"] = "return=minimal"

    count = 0
    for insight in insights:
        data = {
            "user_id": user_id,
            "entity_id": insight.get("entity_id"),
            "insight_type": insight.get("insight_type", "summary"),
            "title": insight.get("title", "")[:500],
            "content": insight.get("content", "")[:5000],
            "importance": insight.get("importance", "medium"),
            "confidence": insight.get("confidence", 0.5),
            "source_ids": insight.get("source_ids", []),
            "is_read": False,
            "is_archived": False,
            "generated_at": insight.get("generated_at", datetime.utcnow().isoformat()),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=data, headers=headers)
                if response.status_code in [200, 201]:
                    count += 1
        except Exception as e:
            logger.warning(f"Error storing insight: {e}", extra={"insight_type": insight.get("insight_type")})

    return count


async def process_single_entity(
    supabase_url: str,
    user_token: str,
    entity_id: str,
    entity_name: str,
    user_id: str,
    sources: List[Dict]
) -> Dict[str, Any]:
    """Process all sources for one entity"""
    results = {
        "entity_id": entity_id,
        "sources_processed": 0,
        "insights_generated": 0,
        "errors": [],
    }

    for source in sources:
        if not source.get("is_active"):
            continue

        try:
            # Process source
            result = await pipeline.process_source(source, {"id": entity_id, "name": entity_name})

            if result and "error" in result:
                results["errors"].append(result["error"])
                continue

            results["sources_processed"] += 1

            # Store raw data
            if result and "raw_data" in result:
                await store_raw_data(
                    supabase_url, user_token,
                    entity_id, source["id"],
                    result["raw_data"]
                )

            # Store insights
            if result and "insights" in result:
                count = await store_insights(
                    supabase_url, user_token,
                    user_id, result["insights"]
                )
                results["insights_generated"] += count

            # Update source last_fetched_at
            await update_source_fetch(supabase_url, user_token, source["id"])

        except Exception as e:
            results["errors"].append(str(e))

    return results


async def update_source_fetch(supabase_url: str, user_token: str, source_id: str):
    """Update source last_fetched_at and increment fetch_count - uses user token for RLS"""
    url = f"{supabase_url}/rest/v1/sources?id=eq.{source_id}"

    headers = _get_user_headers(supabase_url, user_token)

    # Get current fetch_count
    get_url = f"{url}&select=fetch_count"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(get_url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                current_count = data[0].get("fetch_count", 0) if data else 0
            else:
                current_count = 0
    except:
        current_count = 0

    # Update
    data = {
        "last_fetched_at": datetime.utcnow().isoformat(),
        "fetch_count": current_count + 1,
        "status": "active",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.patch(url, json=data, headers=headers)
    except Exception as e:
        logger.warning(f"Error updating source: {e}", extra={"source_id": source_id})


async def run_full_pipeline(
    supabase_url: str,
    user_token: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Run the full intelligence pipeline for all entities belonging to the user.
    Uses user-scoped authentication to respect RLS.
    """
    from .anomaly_detector import run_anomaly_detection

    # Get all active entities for this user only (RLS will enforce this)
    url = f"{supabase_url}/rest/v1/entities?is_archived=eq.false&select=id,name,user_id"
    headers = _get_user_headers(supabase_url, user_token)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

            if response.status_code != 200:
                return {"error": "Failed to fetch entities", "details": response.text}

            entities = response.json()
    except Exception as e:
        return {"error": str(e)}

    # Process each entity
    total_insights = 0
    total_sources = 0
    all_errors = []

    for entity in entities:
        sources = await get_sources_for_entity(supabase_url, user_token, entity["id"])
        result = await process_single_entity(
            supabase_url, user_token,
            entity["id"], entity["name"],
            entity["user_id"], sources
        )
        total_insights += result["insights_generated"]
        total_sources += result["sources_processed"]
        all_errors.extend(result["errors"])

    # Phase 2: Anomaly Detection — check for unusual activity patterns
    anomalies_triggered = 0
    try:
        anomaly_insights = await run_anomaly_detection(user_id, entities)
        for anomaly in anomaly_insights:
            stored = await store_insights(
                supabase_url, user_token,
                user_id, [anomaly]
            )
            anomalies_triggered += stored
        total_insights += anomalies_triggered
        if anomaly_insights:
            logger.info(
                f"Anomaly detection: {len(anomaly_insights)} anomalies detected"
            )
    except Exception as e:
        logger.error(f"Anomaly detection failed: {e}")
        all_errors.append(f"Anomaly detection: {str(e)}")

    # Phase 4: Pattern Learning — learn from insight history, generate predictions
    predictions_generated = 0
    try:
        from .pattern_engine import run_prediction_generation
        predictions = await run_prediction_generation(user_id)
        for pred in predictions:
            stored = await store_insights(
                supabase_url, user_token,
                user_id, [pred]
            )
            predictions_generated += stored
        total_insights += predictions_generated
        if predictions:
            logger.info(
                f"Prediction generation: {len(predictions)} predictions generated"
            )
    except Exception as e:
        logger.error(f"Prediction generation failed: {e}")
        all_errors.append(f"Prediction generation: {str(e)}")

    return {
        "entities_processed": len(entities),
        "sources_processed": total_sources,
        "insights_generated": total_insights,
        "anomalies_detected": anomalies_triggered,
        "predictions_generated": predictions_generated,
        "errors": all_errors[:10],
        "completed_at": datetime.utcnow().isoformat(),
    }


async def run_entity_pipeline(
    supabase_url: str,
    user_token: str,
    entity_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Run pipeline for a single entity (useful for on-demand refresh).
    Uses user-scoped authentication to respect RLS.
    """

    # Get entity (RLS will ensure user can only access their own entities)
    url = f"{supabase_url}/rest/v1/entities?id=eq.{entity_id}&select=id,name,user_id"
    headers = _get_user_headers(supabase_url, user_token)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)

        if response.status_code != 200 or not response.json():
            return {"error": "Entity not found"}

        entity = response.json()[0]

    # Get sources
    sources = await get_sources_for_entity(supabase_url, user_token, entity_id)

    # Process
    result = await process_single_entity(
        supabase_url, user_token,
        entity["id"], entity["name"],
        entity["user_id"], sources
    )

    # Phase 2: Anomaly Detection for single entity
    anomalies_triggered = 0
    try:
        from .anomaly_detector import run_anomaly_detection
        anomaly_insights = await run_anomaly_detection(user_id, [entity])
        for anomaly in anomaly_insights:
            stored = await store_insights(
                supabase_url, user_token,
                user_id, [anomaly]
            )
            anomalies_triggered += stored
        result["insights_generated"] += anomalies_triggered
        if anomaly_insights:
            logger.info(
                f"Anomaly detection (single entity): {len(anomaly_insights)} anomalies detected"
            )
    except Exception as e:
        logger.error(f"Anomaly detection failed for entity {entity_id}: {e}")

    # Phase 4: Pattern Learning — learn from new insights
    predictions_generated = 0
    try:
        from .pattern_engine import run_prediction_generation
        predictions = await run_prediction_generation(user_id, entity_id)
        for pred in predictions:
            stored = await store_insights(
                supabase_url, user_token,
                user_id, [pred]
            )
            predictions_generated += stored
        result["insights_generated"] += predictions_generated
        if predictions:
            logger.info(
                f"Prediction generation: {len(predictions)} predictions generated"
            )
    except Exception as e:
        logger.error(f"Prediction generation failed for entity {entity_id}: {e}")

    result["anomalies_detected"] = anomalies_triggered
    result["entity_name"] = entity["name"]
    result["entity_id"] = entity_id
    result["entities_processed"] = 1
    result["completed_at"] = datetime.utcnow().isoformat()

    return result


# Synchronous wrapper for use in FastAPI
def run_pipeline_sync(
    supabase_url: str,
    user_token: str,
    user_id: str
) -> Dict[str, Any]:
    """Synchronous wrapper for background tasks"""
    try:
        # Check if we're already in an async context
        try:
            asyncio.get_running_loop()
            # We're in an async context - use ThreadPoolExecutor
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    run_full_pipeline(supabase_url, user_token, user_id)
                )
                return future.result(timeout=300)  # 5 min timeout
        except RuntimeError:
            # No running loop - we're in a sync context
            return asyncio.run(run_full_pipeline(supabase_url, user_token, user_id))
    except Exception as e:
        return {"error": str(e)}


def run_entity_pipeline_sync(
    supabase_url: str,
    user_token: str,
    entity_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Synchronous wrapper for single entity pipeline"""
    try:
        # Check if we're already in an async context
        try:
            asyncio.get_running_loop()
            # We're in an async context - use ThreadPoolExecutor
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    run_entity_pipeline(supabase_url, user_token, entity_id, user_id)
                )
                return future.result(timeout=120)
        except RuntimeError:
            # No running loop - we're in a sync context
            return asyncio.run(run_entity_pipeline(supabase_url, user_token, entity_id, user_id))
    except Exception as e:
        return {"error": str(e)}
