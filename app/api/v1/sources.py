from fastapi import APIRouter, Depends, HTTPException, Query, Header
from typing import List, Optional
import httpx
from datetime import datetime
from ..deps import get_current_user, get_supabase, get_user_context, SupabaseClient
from ...schemas.entity import SourceCreate, SourceUpdate, SourceResponse
from ...services.tier_limits import require_source_limit
from ...services.notifier import validate_webhook_url

router = APIRouter(prefix="/sources", tags=["Sources"])


def get_auth_header(authorization: str = Header(None)) -> str:
    """Extract token from Authorization header"""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization or ""


@router.get("", response_model=List[SourceResponse])
async def list_sources(
    entity_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """List all sources for current user"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        # Build params for Supabase REST API
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
        if status_filter:
            params.append(f"status=eq.{status_filter}")

        url = f"{supabase.url}/rest/v1/sources"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            sources = response.json()
            return [SourceResponse(**s) for s in sources]
        return []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=SourceResponse)
async def create_source(
    source: SourceCreate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
    user_context: dict = Depends(get_user_context),
    _: bool = Depends(require_source_limit),
):
    """Create a new data source"""
    try:
        source_type = source.source_type.value if hasattr(source.source_type, 'value') else source.source_type

        data = {
            "user_id": current_user["id"],
            "entity_id": source.entity_id,
            "name": source.name,
            "source_type": source_type,
            "url": source.url,
            "config": source.config or {},
            "refresh_interval_minutes": source.refresh_interval_minutes,
            "status": "active",
        }

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = f"{supabase.url}/rest/v1/sources"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)

        if response.status_code in [200, 201]:
            result = response.json()
            return SourceResponse(**result[0])

        raise HTTPException(status_code=400, detail=f"Failed to create source: {response.text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get source by ID"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        url = supabase.build_url("sources", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return SourceResponse(**data[0])

        raise HTTPException(status_code=404, detail="Source not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: str,
    source: SourceUpdate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Update a source"""
    try:
        update_data = {}
        if source.name is not None:
            update_data["name"] = source.name
        if source.url is not None:
            update_data["url"] = source.url
        if source.config is not None:
            update_data["config"] = source.config
        if source.refresh_interval_minutes is not None:
            update_data["refresh_interval_minutes"] = source.refresh_interval_minutes
        if source.is_active is not None:
            update_data["is_active"] = source.is_active

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = supabase.build_url("sources", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=update_data, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                return SourceResponse(**data[0])

        raise HTTPException(status_code=404, detail="Source not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{source_id}")
async def delete_source(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Delete a source"""
    try:
        headers = supabase._get_headers()
        headers["Prefer"] = "return=minimal"

        url = supabase.build_url("sources", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=headers)

        if response.status_code in [200, 204]:
            return {"message": "Source deleted successfully"}
        raise HTTPException(status_code=404, detail="Source not found or not authorized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{source_id}/test")
async def test_source(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Test source connection"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        
        url = supabase.build_url("sources", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200 or not response.json():
            raise HTTPException(status_code=404, detail="Source not found")

        source = response.json()[0]
        if not source.get("url"):
            return {"success": False, "error": "No URL configured"}

        # SSRF protection: validate URL before making request
        is_valid, error_msg = validate_webhook_url(source["url"])
        if not is_valid:
            return {"success": False, "error": f"Invalid URL: {error_msg}"}

        # Test connection
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                test_response = await client.get(source["url"])
                if test_response.status_code == 200:
                    return {"success": True, "status_code": test_response.status_code}
                return {"success": False, "error": f"HTTP {test_response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/{source_id}/refresh")
async def refresh_source(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Trigger immediate source refresh"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        # Get current fetch_count
        url = supabase.build_url("sources", params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        fetch_count = 1
        if response.status_code == 200 and response.json():
            fetch_count = (response.json()[0].get("fetch_count") or 0) + 1

        # Update with new fetch info
        params = [
            f"id=eq.{source_id}",
            f"user_id=eq.{user_id}",
        ]
        data = {
            "last_fetched_at": datetime.utcnow().isoformat(),
            "fetch_count": fetch_count,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.patch(url, json=data, headers=headers)

        return {"message": "Source refresh triggered"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
