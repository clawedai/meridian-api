"""
Competitive Groups API — CRUD for manual user-created competitive groups.
Part of Pillar 3 (Competitive Benchmarking).

Users can create named groups (e.g. "EV Market") and add entities to them
for custom competitive comparison beyond auto industry grouping.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from typing import List
import httpx
import logging

from ..deps import get_current_user, get_supabase, SupabaseClient
from ...schemas.entity import (
    CompetitiveGroupCreate,
    CompetitiveGroupUpdate,
    CompetitiveGroupResponse,
)
from ...core.config import settings

router = APIRouter(prefix="/competitive-groups", tags=["Competitive Groups"])
logger = logging.getLogger(__name__)


def get_auth_header(authorization: str = Header(None)) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization or ""


def _build_url(base_url: str, params: List[str]) -> str:
    """Build URL with query params from list of 'key=value' strings."""
    if not params:
        return base_url
    import urllib.parse
    pairs = []
    for p in params:
        if '=' in p:
            key, val = p.split('=', 1)
            pairs.append((key, val))
    query = urllib.parse.urlencode(pairs)
    return f"{base_url}?{query}"


@router.get("", response_model=List[CompetitiveGroupResponse])
async def list_groups(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """List all competitive groups for current user."""
    user_id = current_user["id"]
    headers = supabase._get_headers()

    # Fetch groups
    groups_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_groups",
        [f"user_id=eq.{user_id}", "order=created_at.desc"],
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(groups_url, headers=headers)

        if response.status_code != 200:
            return []

        groups = response.json()
        if not groups:
            return []

        # Fetch entity counts per group
        group_ids = [g["id"] for g in groups]
        entities_url = _build_url(
            f"{supabase.url}/rest/v1/competitive_group_entities",
            [f"group_id=in.({','.join(group_ids)})", "select=group_id"],
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            rel_response = await client.get(entities_url, headers=headers)

        entity_counts = {}
        if rel_response.status_code == 200:
            from collections import Counter
            rels = rel_response.json()
            counts = Counter(r["group_id"] for r in rels)
            entity_counts = dict(counts)

        return [
            CompetitiveGroupResponse(
                id=g["id"],
                user_id=g["user_id"],
                name=g["name"],
                entity_count=entity_counts.get(g["id"], 0),
                created_at=g.get("created_at", ""),
            )
            for g in groups
        ]
    except Exception as e:
        logger.error(f"Error listing competitive groups: {e}")
        raise HTTPException(status_code=500, detail="Failed to list groups")


@router.post("", response_model=CompetitiveGroupResponse)
async def create_group(
    group: CompetitiveGroupCreate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Create a new competitive group."""
    user_id = current_user["id"]
    headers = supabase._get_headers()
    headers["Prefer"] = "return=representation"

    # Verify all entity IDs belong to this user
    if group.entity_ids:
        entity_ids_str = ",".join(group.entity_ids)
        verify_url = _build_url(
            f"{supabase.url}/rest/v1/entities",
            [f"id=in.({entity_ids_str})", f"user_id=eq.{user_id}", "select=id"],
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            verify_resp = await client.get(verify_url, headers=headers)

        if verify_resp.status_code == 200:
            valid_ids = {e["id"] for e in verify_resp.json()}
            invalid = set(group.entity_ids) - valid_ids
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Entity IDs not found or not owned: {list(invalid)[:3]}",
                )

    # Create the group
    url = f"{supabase.url}/rest/v1/competitive_groups"
    data = {"user_id": user_id, "name": group.name}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)

        if response.status_code not in [200, 201]:
            raise HTTPException(status_code=400, detail=f"Failed to create group: {response.text}")

        result = response.json()
        group_id = result[0]["id"]

        # Add entity associations
        if group.entity_ids:
            associations = [
                {"group_id": group_id, "entity_id": eid}
                for eid in group.entity_ids
            ]
            assoc_url = f"{supabase.url}/rest/v1/competitive_group_entities"
            assoc_headers = {**headers, "Prefer": "return=minimal"}
            async with httpx.AsyncClient(timeout=30.0) as assoc_client:
                await assoc_client.post(assoc_url, json=associations, headers=assoc_headers)

        return CompetitiveGroupResponse(
            id=group_id,
            user_id=user_id,
            name=group.name,
            entity_count=len(group.entity_ids),
            created_at=result[0].get("created_at", ""),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating competitive group: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{group_id}", response_model=CompetitiveGroupResponse)
async def get_group(
    group_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Get a competitive group by ID."""
    user_id = current_user["id"]
    headers = supabase._get_headers()

    params = [f"id=eq.{group_id}", f"user_id=eq.{user_id}"]
    url = _build_url(f"{supabase.url}/rest/v1/competitive_groups", params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)

    if response.status_code != 200 or not response.json():
        raise HTTPException(status_code=404, detail="Group not found")

    group = response.json()[0]

    # Count entities
    count_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_group_entities",
        [f"group_id=eq.{group_id}", "select=entity_id"],
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        count_response = await client.get(count_url, headers=headers)
    entity_count = 0
    if count_response.status_code == 200:
        entity_count = len(count_response.json())

    return CompetitiveGroupResponse(
        id=group["id"],
        user_id=group["user_id"],
        name=group["name"],
        entity_count=entity_count,
        created_at=group.get("created_at", ""),
    )


@router.patch("/{group_id}", response_model=CompetitiveGroupResponse)
async def update_group(
    group_id: str,
    group: CompetitiveGroupUpdate,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Update a competitive group (name or entity membership)."""
    user_id = current_user["id"]
    headers = supabase._get_headers()

    # Verify ownership
    verify_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_groups",
        [f"id=eq.{group_id}", f"user_id=eq.{user_id}", "select=id"],
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        verify_resp = await client.get(verify_url, headers=headers)

    if verify_resp.status_code != 200 or not verify_resp.json():
        raise HTTPException(status_code=404, detail="Group not found")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Update name if provided
        if group.name is not None:
            update_data = {"name": group.name}
            update_url = _build_url(
                f"{supabase.url}/rest/v1/competitive_groups",
                [f"id=eq.{group_id}", f"user_id=eq.{user_id}"],
            )
            headers["Prefer"] = "return=representation"
            patch_resp = await client.patch(update_url, json=update_data, headers=headers)
            if patch_resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to update group name")

        # Update entity membership if provided
        if group.entity_ids is not None:
            # Verify all entity IDs belong to this user
            if group.entity_ids:
                entity_ids_str = ",".join(group.entity_ids)
                verify_entities_url = _build_url(
                    f"{supabase.url}/rest/v1/entities",
                    [f"id=in.({entity_ids_str})", f"user_id=eq.{user_id}", "select=id"],
                )
                verify_ent_resp = await client.get(verify_entities_url, headers=headers)
                if verify_ent_resp.status_code == 200:
                    valid_ids = {e["id"] for e in verify_ent_resp.json()}
                    invalid = set(group.entity_ids) - valid_ids
                    if invalid:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Entity IDs not found or not owned: {list(invalid)[:3]}",
                        )

            # Delete existing associations
            del_url = _build_url(
                f"{supabase.url}/rest/v1/competitive_group_entities",
                [f"group_id=eq.{group_id}"],
            )
            del_headers = {**headers, "Prefer": "return=minimal"}
            await client.delete(del_url, headers=del_headers)

            # Insert new associations
            if group.entity_ids:
                associations = [
                    {"group_id": group_id, "entity_id": eid}
                    for eid in group.entity_ids
                ]
                assoc_url = f"{supabase.url}/rest/v1/competitive_group_entities"
                assoc_headers = {**headers, "Prefer": "return=minimal"}
                await client.post(assoc_url, json=associations, headers=assoc_headers)

    # Fetch updated group
    final_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_groups",
        [f"id=eq.{group_id}", f"user_id=eq.{user_id}"],
    )
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=30.0) as client:
        final_resp = await client.get(final_url, headers=headers)

    if final_resp.status_code != 200 or not final_resp.json():
        raise HTTPException(status_code=404, detail="Group not found")

    result = final_resp.json()[0]

    # Count entities
    count_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_group_entities",
        [f"group_id=eq.{group_id}", "select=entity_id"],
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        count_response = await client.get(count_url, headers=headers)
    entity_count = 0
    if count_response.status_code == 200:
        entity_count = len(count_response.json())

    return CompetitiveGroupResponse(
        id=result["id"],
        user_id=result["user_id"],
        name=result["name"],
        entity_count=entity_count,
        created_at=result.get("created_at", ""),
    )


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    authorization: str = Depends(get_auth_header),
):
    """Delete a competitive group and its associations."""
    user_id = current_user["id"]
    headers = supabase._get_headers()
    headers["Prefer"] = "return=minimal"

    # Verify ownership
    verify_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_groups",
        [f"id=eq.{group_id}", f"user_id=eq.{user_id}", "select=id"],
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        verify_resp = await client.get(verify_url, headers=headers)

    if verify_resp.status_code != 200 or not verify_resp.json():
        raise HTTPException(status_code=404, detail="Group not found")

    # Delete associations first (cascade)
    del_assn_url = _build_url(
        f"{supabase.url}/rest/v1/competitive_group_entities",
        [f"group_id=eq.{group_id}"],
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(del_assn_url, headers=headers)
        # Delete group
        del_url = _build_url(
            f"{supabase.url}/rest/v1/competitive_groups",
            [f"id=eq.{group_id}", f"user_id=eq.{user_id}"],
        )
        del_resp = await client.delete(del_url, headers=headers)

    if del_resp.status_code in [200, 204]:
        return {"message": "Group deleted successfully"}
    raise HTTPException(status_code=400, detail="Failed to delete group")
