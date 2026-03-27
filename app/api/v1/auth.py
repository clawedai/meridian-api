"""
Custom Authentication — bypasses broken Supabase Auth.
Uses public.users table as the auth source of truth.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import Optional
from pydantic import BaseModel, EmailStr
import asyncio
import uuid
import logging
import httpx
from datetime import datetime, timezone

from ..deps import get_supabase, SupabaseClient
from ...schemas.user import Token, UserResponse
from ...core.security import create_access_token, verify_password, get_password_hash

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    company_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register", response_model=Token)
async def register(
    request: RegisterRequest,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Register a new user with email/password"""
    # Validate password strength
    if len(request.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    try:
        # Check if email already exists — query by email, not all rows
        check_headers = {
            "apikey": supabase.anon_key,
            "Authorization": f"Bearer {supabase.service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            existing_resp = await client.get(
                f"{supabase.url}/rest/v1/users?email=eq.{request.email}&select=id",
                headers=check_headers,
            )
        if existing_resp.status_code == 200 and existing_resp.text.strip():
            existing_users = existing_resp.json()
            if isinstance(existing_users, list) and len(existing_users) > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An account with this email already exists",
                )

        # Create user ID
        user_id = str(uuid.uuid4())
        password_hash = get_password_hash(request.password)
        now = datetime.now(timezone.utc).isoformat()

        # Insert into public.users
        user_row = {
            "id": user_id,
            "email": request.email,
            "full_name": request.full_name or "",
            "company_name": request.company_name or "",
            "password_hash": password_hash,
            "subscription_tier": "starter",
            "subscription_status": "trialing",
            "created_at": now,
            "updated_at": now,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            users_resp = await client.post(
                f"{supabase.url}/rest/v1/users",
                json=user_row,
                headers=check_headers,
            )
        if users_resp.status_code not in (200, 201):
            logger.error(f"Users insert failed: {users_resp.status_code} — {users_resp.text}")
            raise HTTPException(status_code=400, detail=f"Failed to create user: {users_resp.text[:100]}")

        # Insert into public.profiles
        profile_row = {
            "id": user_id,
            "email": request.email,
            "full_name": request.full_name or "",
            "company_name": request.company_name or "",
            "subscription_tier": "starter",
            "subscription_status": "trialing",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            profiles_resp = await client.post(
                f"{supabase.url}/rest/v1/profiles",
                json=profile_row,
                headers=check_headers,
            )
        if profiles_resp.status_code not in (200, 201):
            logger.error(f"Profiles insert failed: {profiles_resp.status_code} — {profiles_resp.text}")
            raise HTTPException(status_code=400, detail=f"Failed to create profile: {profiles_resp.text[:100]}")

        # Generate JWT
        access_token = create_access_token({"sub": user_id})

        logger.info(f"User registered: {request.email} ({user_id})")

        return Token(
            access_token=access_token,
            user=UserResponse(
                id=user_id,
                email=request.email,
                full_name=request.full_name,
                company_name=request.company_name,
                created_at=now,
                updated_at=now,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error for {request.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Registration failed: {str(e)[:100]}",
        )


@router.post("/login", response_model=Token)
async def login(
    request: LoginRequest,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Login with email/password, returns JWT"""
    try:
        # Query public.users for this email
        headers = {
            "apikey": supabase.anon_key,
            "Authorization": f"Bearer {supabase.service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{supabase.url}/rest/v1/users?email=eq.{request.email}&select=*",
                headers=headers,
            )

        if resp.status_code != 200 or not resp.text.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        users = resp.json()
        if not isinstance(users, list) or len(users) == 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        user = users[0]
        password_hash = user.get("password_hash")
        if not password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        if not verify_password(request.password, password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        user_id = user["id"]

        # Get profile data
        profile_data = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                profile_resp = await client.get(
                    f"{supabase.url}/rest/v1/profiles?id=eq.{user_id}&select=*",
                    headers=headers,
                )
            if profile_resp.status_code == 200 and profile_resp.text.strip():
                profiles = profile_resp.json()
                if isinstance(profiles, list) and len(profiles) > 0:
                    profile_data = profiles[0]
        except Exception:
            pass

        # Generate JWT
        access_token = create_access_token({"sub": user_id})

        logger.info(f"User logged in: {request.email} ({user_id})")

        return Token(
            access_token=access_token,
            user=UserResponse(
                id=user_id,
                email=user.get("email", request.email),
                full_name=user.get("full_name"),
                company_name=user.get("company_name"),
                company_industry=profile_data.get("company_industry"),
                avatar_url=profile_data.get("avatar_url"),
                subscription_tier=profile_data.get("subscription_tier", "starter"),
                subscription_status=profile_data.get("subscription_status", "trialing"),
                created_at=profile_data.get("created_at", user.get("created_at")),
                updated_at=profile_data.get("updated_at", user.get("updated_at")),
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error for {request.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )


@router.post("/logout")
async def logout():
    """Logout — client discards the JWT."""
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(
    authorization: Optional[str] = Header(None),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get current user profile using JWT"""
    # Get token from header
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )
    token = authorization[7:] if authorization.startswith("Bearer ") else authorization

    from ...core.security import verify_token
    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Fetch user + profile
    headers = {
        "apikey": supabase.anon_key,
        "Authorization": f"Bearer {supabase.service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            user_resp, profile_resp = await asyncio.gather(
                client.get(
                    f"{supabase.url}/rest/v1/users?id=eq.{user_id}&select=*",
                    headers=headers,
                ),
                client.get(
                    f"{supabase.url}/rest/v1/profiles?id=eq.{user_id}&select=*",
                    headers=headers,
                ),
            )

        user_data = {}
        if user_resp.status_code == 200 and user_resp.text.strip():
            users = user_resp.json()
            if isinstance(users, list) and len(users) > 0:
                user_data = users[0]

        profile_data = {}
        if profile_resp.status_code == 200 and profile_resp.text.strip():
            profiles = profile_resp.json()
            if isinstance(profiles, list) and len(profiles) > 0:
                profile_data = profiles[0]

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return UserResponse(
            id=user_id,
            email=user_data.get("email"),
            full_name=user_data.get("full_name"),
            company_name=user_data.get("company_name"),
            company_industry=profile_data.get("company_industry"),
            avatar_url=profile_data.get("avatar_url"),
            subscription_tier=profile_data.get("subscription_tier", "starter"),
            subscription_status=profile_data.get("subscription_status", "trialing"),
            created_at=profile_data.get("created_at", user_data.get("created_at")),
            updated_at=profile_data.get("updated_at", user_data.get("updated_at")),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get me error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user",
        )
