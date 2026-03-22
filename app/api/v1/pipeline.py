"""
Pipeline API endpoints - Trigger intelligence collection and analysis
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import List, Optional
from enum import Enum
from ..deps import get_current_user, get_supabase, SupabaseClient
from ...core.config import settings
from ...core.security import verify_token

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


class TriggerPipelineRequest(BaseModel):
    entity_id: Optional[str] = None  # If None, processes all entities


class PipelineStatusResponse(BaseModel):
    entities_processed: int
    sources_processed: int
    insights_generated: int
    anomalies_detected: int = 0  # Phase 2: anomaly insights found
    predictions_generated: int = 0  # Phase 4: prediction insights found
    errors: List[str]
    completed_at: str
    entity_name: Optional[str] = None
    entity_id: Optional[str] = None


def get_user_token(authorization: Optional[str] = Header(None)) -> str:
    """Extract user token from Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization


@router.post("/trigger", response_model=PipelineStatusResponse)
async def trigger_pipeline(
    request: Optional[TriggerPipelineRequest] = None,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: Optional[str] = Header(None)
):
    """
    Trigger the intelligence pipeline.

    If entity_id is provided, processes only that entity.
    Otherwise, processes all active entities.

    SECURITY: Uses user's JWT token to respect Row Level Security.
    """
    try:
        from ...services.processor import (
            run_entity_pipeline_sync,
            run_pipeline_sync
        )

        supabase_url = settings.SUPABASE_URL

        # Get user's JWT token for RLS-compliant access
        user_token = get_user_token(authorization)
        user_id = current_user["id"]

        if request and request.entity_id:
            # Process single entity
            result = run_entity_pipeline_sync(
                supabase_url, user_token, request.entity_id, user_id
            )
        else:
            # Process all user entities
            result = run_pipeline_sync(supabase_url, user_token, user_id)

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return PipelineStatusResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_pipeline_status(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get pipeline status (last run info, etc.)"""
    # For now, return a simple status
    return {
        "status": "ready",
        "message": "Pipeline is ready to process"
    }
