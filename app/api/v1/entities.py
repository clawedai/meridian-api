from fastapi import APIRouter, Depends, HTTPException, Query, Header
from typing import List, Optional
import httpx
import logging
import urllib.parse
from ..deps import get_current_user, get_supabase, get_user_context, SupabaseClient
from ...schemas.entity import EntityCreate, EntityUpdate, EntityResponse
from ...core.config import settings
from ...services.tier_limits import require_entity_limit

router = APIRouter(prefix="/entities", tags=["Entities"])


def get_auth_header(authorization: str = Header(None)) -> str:
    """Extract token from Authorization header"""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization or ""


def _build_url(base_url: str, params: List[str]) -> str:
    """Build URL with query params from list of 'key=value' strings."""
    if not params:
        return base_url
    pairs = []
    for p in params:
        if '=' in p:
            key, val = p.split('=', 1)
            pairs.append((key, val))
    query = urllib.parse.urlencode(pairs)
    return f"{base_url}?{query}"


@router.get("", response_model=List[EntityResponse])
async def list_entities(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = False,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """List all entities for current user"""
    try:
        user_id = current_user["id"]
        params = [
            f"user_id=eq.{user_id}",
            "order=created_at.desc",
            f"limit={limit}",
            f"offset={skip}",
        ]
        if not include_archived:
            params.append("is_archived=eq.false")

        url = _build_url(f"{supabase.url}/rest/v1/entities", params)
        headers = supabase._get_headers()
        headers["Prefer"] = "count=none"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            entities = response.json()
            return [EntityResponse(**e) for e in entities]
        return []
    except Exception as e:
        logging.error(f"Error in list_entities: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list entities")


@router.post("", response_model=EntityResponse)
async def create_entity(
    entity: EntityCreate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
    user_context: dict = Depends(get_user_context),
    _: bool = Depends(require_entity_limit),
):
    """Create a new entity"""
    try:
        data = {
            "user_id": current_user["id"],
            "name": entity.name,
            "website": entity.website,
            "industry": entity.industry,
            "description": entity.description,
            "tags": entity.tags or [],
        }

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"
        headers["Authorization"] = f"Bearer {authorization}"

        url = f"{supabase.url}/rest/v1/entities"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)

        if response.status_code in [200, 201]:
            result = response.json()
            return EntityResponse(**result[0])

        raise HTTPException(status_code=400, detail=f"Failed to create entity: {response.text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get entity by ID"""
    try:
        user_id = current_user["id"]
        params = [f"id=eq.{entity_id}", f"user_id=eq.{user_id}"]
        url = _build_url(f"{supabase.url}/rest/v1/entities", params)
        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return EntityResponse(**data[0])

        raise HTTPException(status_code=404, detail="Entity not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update_entity(
    entity_id: str,
    entity: EntityUpdate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Update an entity"""
    try:
        user_id = current_user["id"]
        params = [f"id=eq.{entity_id}", f"user_id=eq.{user_id}"]
        update_data = {k: v for k, v in entity.model_dump().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = _build_url(f"{supabase.url}/rest/v1/entities", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=update_data, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return EntityResponse(**data[0])

        raise HTTPException(status_code=404, detail="Entity not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{entity_id}")
async def delete_entity(
    entity_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Archive an entity (soft delete)"""
    try:
        user_id = current_user["id"]
        params = [f"id=eq.{entity_id}", f"user_id=eq.{user_id}"]
        headers = supabase._get_headers()
        headers["Prefer"] = "return=minimal"

        url = _build_url(f"{supabase.url}/rest/v1/entities", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json={"is_archived": True}, headers=headers)

        if response.status_code in [200, 204]:
            return {"message": "Entity archived successfully"}
        raise HTTPException(status_code=404, detail="Entity not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{entity_id}/sources")
async def get_entity_sources(
    entity_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get all sources for an entity"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        # Verify entity ownership
        entity_params = [f"id=eq.{entity_id}", f"user_id=eq.{user_id}"]
        entity_url = _build_url(f"{supabase.url}/rest/v1/entities", entity_params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            entity_response = await client.get(entity_url, headers=headers)
            if entity_response.status_code != 200 or not entity_response.json():
                raise HTTPException(status_code=404, detail="Entity not found or not authorized")

        # Get sources
        sources_params = [f"entity_id=eq.{entity_id}", f"user_id=eq.{user_id}", "order=created_at.desc"]
        sources_url = _build_url(f"{supabase.url}/rest/v1/sources", sources_params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            sources_response = await client.get(sources_url, headers=headers)

        if sources_response.status_code == 200:
            return sources_response.json()
        return []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{entity_id}/insights")
async def get_entity_insights(
    entity_id: str,
    limit: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get all insights for an entity"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        # Verify entity ownership
        entity_params = [f"id=eq.{entity_id}", f"user_id=eq.{user_id}"]
        entity_url = _build_url(f"{supabase.url}/rest/v1/entities", entity_params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            entity_response = await client.get(entity_url, headers=headers)
            if entity_response.status_code != 200 or not entity_response.json():
                raise HTTPException(status_code=404, detail="Entity not found or not authorized")

        # Get insights
        insights_params = [
            f"entity_id=eq.{entity_id}",
            f"user_id=eq.{user_id}",
            "is_archived=eq.false",
            "order=generated_at.desc",
            f"limit={limit}",
        ]
        insights_url = _build_url(f"{supabase.url}/rest/v1/insights", insights_params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(insights_url, headers=headers)

        if response.status_code == 200:
            return response.json()
        return []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
