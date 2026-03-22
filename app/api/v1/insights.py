from fastapi import APIRouter, Depends, Query
from typing import List, Optional
import httpx
from ..deps import get_current_user, get_supabase, SupabaseClient
from ...schemas.entity import InsightResponse, InsightUpdate, PredictionInsight, PredictionStats

router = APIRouter(prefix="/insights", tags=["Insights"])

@router.get("", response_model=List[InsightResponse])
async def list_insights(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    entity_id: Optional[str] = None,
    importance: Optional[str] = None,
    is_read: Optional[bool] = None,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """List insights for current user with optional filters"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        filters = [f"user_id=eq.{user_id}", "is_archived=eq.false"]
        if entity_id:
            filters.append(f"entity_id=eq.{entity_id}")
        if importance:
            filters.append(f"importance=eq.{importance}")
        if is_read is not None:
            filters.append(f"is_read=eq.{str(is_read).lower()}")

        query = "&".join(filters) + f"&order=generated_at.desc&limit={limit}&offset={skip}"

        url = f"{supabase.url}/rest/v1/insights?{query}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        if response.status_code == 200:
            insights = response.json()
            return [InsightResponse(**i) for i in insights]
        return []
    except Exception as e:
        return []


@router.get("/predictions", response_model=List[InsightResponse])
async def list_prediction_insights(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """List prediction-type insights only (Pillar 4)."""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        filters = [
            f"user_id=eq.{user_id}",
            "is_archived=eq.false",
            "insight_type=eq.prediction",
        ]
        if entity_id:
            filters.append(f"entity_id=eq.{entity_id}")

        query = "&".join(filters) + f"&order=generated_at.desc&limit={limit}&offset={skip}"

        url = f"{supabase.url}/rest/v1/insights?{query}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        if response.status_code == 200:
            insights = response.json()
            return [InsightResponse(**i) for i in insights]
        return []
    except Exception as e:
        return []


@router.get("/stats/predictions", response_model=PredictionStats)
async def get_prediction_stats(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get prediction statistics — how many patterns learned, predictions generated."""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        # Count prediction insights
        url = f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&insight_type=eq.prediction&is_archived=eq.false&select=id,confidence,entity_id"
        async with httpx.AsyncClient(timeout=30.0) as client:
            pred_resp = await client.get(url, headers=headers)

        predictions = pred_resp.json() if pred_resp.status_code == 200 else []
        total_predictions = len(predictions)
        high_conf = sum(1 for p in predictions if p.get("confidence", 0) > 0.7)
        unique_entities = len({p.get("entity_id") for p in predictions if p.get("entity_id")})

        # Count patterns
        pattern_url = f"{supabase.url}/rest/v1/entity_patterns?user_id=eq.{user_id}&select=id"
        pattern_resp = await client.get(pattern_url, headers=headers)
        patterns = pattern_resp.json() if pattern_resp.status_code == 200 else []

        return PredictionStats(
            total_predictions=total_predictions,
            high_confidence_predictions=high_conf,
            predictions_by_entity=unique_entities,
            pattern_count=len(patterns),
            most_common_patterns=[],
        )
    except Exception as e:
        return PredictionStats()

@router.get("/{insight_id}", response_model=InsightResponse)
async def get_insight(
    insight_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get insight by ID"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        url = f"{supabase.url}/rest/v1/insights?id=eq.{insight_id}&user_id=eq.{user_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        if response.status_code == 200:
            data = response.json()
            if data:
                return InsightResponse(**data[0])

        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Insight not found")
    except HTTPException:
        raise
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{insight_id}", response_model=InsightResponse)
async def update_insight(
    insight_id: str,
    insight: InsightUpdate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Update insight (mark as read, archive)"""
    try:
        update_data = {k: v for k, v in insight.model_dump().items() if v is not None}
        if not update_data:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="No fields to update")

        result = supabase.table("insights").update(
            update_data,
            filters=[("id", insight_id), ("user_id", current_user["id"])]
        )
        if result:
            return InsightResponse(**result[0])

        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Insight not found")
    except HTTPException:
        raise
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{insight_id}/mark-read")
async def mark_insight_read(
    insight_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Mark insight as read"""
    try:
        supabase.table("insights").update(
            {"is_read": True},
            filters=[("id", insight_id), ("user_id", current_user["id"])]
        )
        return {"message": "Insight marked as read"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mark-all-read")
async def mark_all_insights_read(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Mark all insights as read"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        headers["Prefer"] = "return=minimal"

        url = f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&is_read=eq.false"
        data = {"is_read": True}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=data, headers=headers)
            response.raise_for_status()

        return {"message": "All insights marked as read"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/unread-count")
async def get_unread_count(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get count of unread insights"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        url = f"{supabase.url}/rest/v1/insights?user_id=eq.{user_id}&is_read=eq.false&is_archived=eq.false&select=id"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        count = len(response.json()) if response.status_code == 200 else 0
        return {"count": count}
    except Exception as e:
        return {"count": 0}
