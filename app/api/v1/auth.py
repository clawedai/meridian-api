from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from pydantic import BaseModel

from ..deps import get_supabase, SupabaseClient, get_current_user
from ...schemas.user import Token, UserResponse
from ...core.security import create_access_token

router = APIRouter(prefix="/auth", tags=["Authentication"])

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    company_name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/register", response_model=Token)
async def register(
    request: RegisterRequest,
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Register a new user"""
    try:
        print(f"Starting registration for: {request.email}")

        user_metadata = {}
        if request.full_name:
            user_metadata["full_name"] = request.full_name
        if request.company_name:
            user_metadata["company_name"] = request.company_name

        print(f"Calling supabase.sign_up...")
        auth_response = supabase.sign_up(
            email=request.email,
            password=request.password,
            user_metadata=user_metadata if user_metadata else None
        )

        print(f"auth_response: {auth_response}")

        # Check for errors - deps.py returns {"error": "message"}
        if "error" in auth_response:
            error_msg = auth_response["error"]
            print(f"Signup error: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        # Supabase returns the user at the top level (not nested under "user")
        # The response can be: {"id": ..., "email": ...} or {"error": "..."}
        user = auth_response.get("user") or auth_response

        if not user or "id" not in user:
            # Check if it's an error response
            if "error" in auth_response:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=auth_response["error"],
                )
            # Generic failure
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to create user",
            )

        session = auth_response.get("session") or {}
        print(f"User created: {user['id']}")

        # Create access token using JWT (your own token for API auth)
        access_token = create_access_token({"sub": user["id"]})

        return Token(
            access_token=access_token,
            user=UserResponse(
                id=user["id"],
                email=user["email"],
                full_name=request.full_name,
                company_name=request.company_name,
                created_at=user.get("created_at"),
                updated_at=user.get("updated_at", user.get("created_at")),
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

@router.post("/login", response_model=Token)
async def login(
    request: LoginRequest,
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Login and get access token"""
    try:
        auth_response = supabase.sign_in(
            email=request.email,
            password=request.password
        )

        # Handle error responses — Supabase can return {"error": ...} or {"error_code": ...}
        if "error" in auth_response or "error_code" in auth_response:
            error_code = auth_response.get("error_code", "")
            error_msg = (
                auth_response.get("error_description") or
                auth_response.get("msg") or
                auth_response.get("error") or
                "Invalid credentials"
            )
            if error_code == "email_not_confirmed":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Email not confirmed. Please check your inbox and click the confirmation link.",
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_msg,
            )

        # Supabase returns user at top level (not nested under "user")
        session = auth_response.get("session", {})
        user = auth_response.get("user") or auth_response

        if not user or "id" not in user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Login failed",
            )

        # Create custom JWT for consistent session management (same as register)
        access_token = create_access_token({"sub": user["id"]})

        # Get profile data
        profile_data = {}
        try:
            profiles = supabase.table("profiles").select("*", token=session.get("access_token")).select("*")
            if isinstance(profiles, list) and len(profiles) > 0:
                profile_data = profiles[0]
            elif isinstance(profiles, dict) and "data" in profiles:
                profile_data = profiles.get("data", {})
        except:
            pass

        return Token(
            access_token=access_token,  # Consistent with register endpoint
            user=UserResponse(
                id=user["id"],
                email=user["email"],
                full_name=profile_data.get("full_name"),
                company_name=profile_data.get("company_name"),
                company_industry=profile_data.get("company_industry"),
                avatar_url=profile_data.get("avatar_url"),
                subscription_tier=profile_data.get("subscription_tier", "starter"),
                subscription_status=profile_data.get("subscription_status", "trialing"),
                created_at=profile_data.get("created_at", user.get("created_at")),
                updated_at=profile_data.get("updated_at", user.get("updated_at")),
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

@router.post("/logout")
async def logout():
    """Logout current user"""
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get current user profile"""
    try:
        # Get profile
        profiles = supabase.table("profiles").select("*", token=current_user.get("id", ""))
        profile_data = {}
        if isinstance(profiles, list) and len(profiles) > 0:
            profile_data = profiles[0]
        elif isinstance(profiles, dict) and "data" in profiles:
            profile_data = profiles.get("data", {})

        return UserResponse(
            id=current_user.get("id"),
            email=current_user.get("email"),
            full_name=profile_data.get("full_name"),
            company_name=profile_data.get("company_name"),
            company_industry=profile_data.get("company_industry"),
            avatar_url=profile_data.get("avatar_url"),
            subscription_tier=profile_data.get("subscription_tier", "starter"),
            subscription_status=profile_data.get("subscription_status", "trialing"),
            created_at=profile_data.get("created_at", current_user.get("created_at")),
            updated_at=profile_data.get("updated_at", current_user.get("updated_at")),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
