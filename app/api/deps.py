from fastapi import Depends, HTTPException, status, Header
from typing import Optional, List, Dict, Any
import httpx
from ..core.config import settings
from ..core.security import verify_token

# Shared async client for concurrent requests
_http_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a shared AsyncClient instance."""
    global _http_async_client
    if _http_async_client is None or _http_async_client.is_closed:
        _http_async_client = httpx.AsyncClient(timeout=30.0)
    return _http_async_client

async def close_async_client():
    """Close the shared AsyncClient."""
    global _http_async_client
    if _http_async_client is not None and not _http_async_client.is_closed:
        await _http_async_client.aclose()
        _http_async_client = None

class SupabaseClient:
    """Simple Supabase REST API client using httpx"""

    def __init__(self):
        self.url = settings.SUPABASE_URL
        self.anon_key = settings.SUPABASE_KEY
        self.service_key = settings.SUPABASE_SERVICE_KEY

    def build_url(self, table: str, params: list = None) -> str:
        """Build a URL for the Supabase REST API with proper query params.

        Args:
            table: Table name (e.g. 'entities')
            params: List of 'key=value' strings, e.g. ['user_id=eq.123', 'order=created_at.desc']
        """
        import urllib.parse
        url = f"{self.url}/rest/v1/{table}"
        if params:
            pairs = []
            for p in params:
                if '=' in p:
                    key, val = p.split('=', 1)
                    pairs.append((key, val))
            query = urllib.parse.urlencode(pairs)
            url = f"{url}?{query}"
        return url

    def _get_headers(self, token: str = None) -> dict:
        headers = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_admin_headers(self) -> dict:
        return {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }

    def sign_up(self, email: str, password: str, user_metadata: dict = None):
        """Create a new user"""
        url = f"{self.url}/auth/v1/signup"
        data = {
            "email": email,
            "password": password,
        }
        if user_metadata:
            data["options"] = {"data": user_metadata}

        print(f"DEBUG: Sending signup request to {url}")
        print(f"DEBUG: Payload: {data}")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=data, headers=self._get_headers())
                # Log for debugging
                print(f"DEBUG: Response status: {response.status_code}")
                print(f"DEBUG: Response body: {response.text}")

                if response.status_code >= 400:
                    # Return error format
                    try:
                        error_data = response.json()
                        error_msg = (
                            error_data.get("error_description") or
                            error_data.get("message") or
                            error_data.get("msg") or
                            error_data.get("error") or
                            str(error_data)
                        )
                        return {"error": error_msg}
                    except:
                        return {"error": f"HTTP {response.status_code}: {response.text}"}

                result = response.json()
                print(f"DEBUG: Parsed JSON: {result}")

                # Check what we got
                if "user" in result:
                    print(f"DEBUG: User found in response")
                elif "error" in result:
                    print(f"DEBUG: Error found in response")
                else:
                    print(f"DEBUG: Unexpected response format!")

                return result
        except Exception as e:
            print(f"DEBUG: Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    async def async_sign_up(self, email: str, password: str, user_metadata: dict = None):
        """Create a new user (async version)"""
        url = f"{self.url}/auth/v1/signup"
        data = {
            "email": email,
            "password": password,
        }
        if user_metadata:
            data["options"] = {"data": user_metadata}

        print(f"DEBUG: Sending signup request to {url}")
        print(f"DEBUG: Payload: {data}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=data, headers=self._get_headers())
                # Log for debugging
                print(f"DEBUG: Response status: {response.status_code}")
                print(f"DEBUG: Response body: {response.text}")

                if response.status_code >= 400:
                    # Return error format
                    try:
                        error_data = response.json()
                        error_msg = (
                            error_data.get("error_description") or
                            error_data.get("message") or
                            error_data.get("msg") or
                            error_data.get("error") or
                            str(error_data)
                        )
                        return {"error": error_msg}
                    except:
                        return {"error": f"HTTP {response.status_code}: {response.text}"}

                result = response.json()
                print(f"DEBUG: Parsed JSON: {result}")

                # Check what we got
                if "user" in result:
                    print(f"DEBUG: User found in response")
                elif "error" in result:
                    print(f"DEBUG: Error found in response")
                else:
                    print(f"DEBUG: Unexpected response format!")

                return result
        except Exception as e:
            print(f"DEBUG: Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    def sign_in(self, email: str, password: str):
        """Sign in existing user"""
        url = f"{self.url}/auth/v1/token?grant_type=password"
        data = {
            "email": email,
            "password": password,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=data, headers=self._get_headers())
            return response.json()

    async def async_sign_in(self, email: str, password: str):
        """Sign in existing user (async version)"""
        url = f"{self.url}/auth/v1/token?grant_type=password"
        data = {
            "email": email,
            "password": password,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    def get_user(self, token: str):
        """Get user info from token"""
        url = f"{self.url}/auth/v1/user"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._get_headers(token))
            if response.status_code == 200:
                return response.json()
            return None

    async def async_get_user(self, token: str):
        """Get user info from token (async version)"""
        url = f"{self.url}/auth/v1/user"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._get_headers(token))
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return None

    def admin_get_user(self, user_id: str):
        """Admin: Get user by ID"""
        url = f"{self.url}/auth/v1/admin/users/{user_id}"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._get_admin_headers())
            if response.status_code == 200:
                return response.json()
            return None

    async def async_admin_get_user(self, user_id: str):
        """Admin: Get user by ID (async version)"""
        url = f"{self.url}/auth/v1/admin/users/{user_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._get_admin_headers())
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return None

    def table(self, table_name: str):
        """Get a table reference"""
        return TableOperations(self, table_name)

    def async_table(self, table_name: str):
        """Get an async table reference"""
        return TableOperationsAsync(self, table_name)

class TableOperations:
    """Table CRUD operations via REST API"""

    def __init__(self, client: SupabaseClient, table_name: str):
        self.client = client
        self.table_name = table_name
        self.base_url = f"{client.url}/rest/v1/{table_name}"

    def select(self, query: str = "*", token: str = None) -> List[Dict]:
        """SELECT rows"""
        url = f"{self.base_url}?select={query}"
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        with httpx.Client(timeout=30.0) as http:
            response = http.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            return []

    async def async_select(self, query: str = "*", token: str = None) -> List[Dict]:
        """SELECT rows (async version)"""
        url = f"{self.base_url}?select={query}"
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(url, headers=headers)
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return []

    def insert(self, data: dict, token: str = None) -> List[Dict]:
        """INSERT row(s)"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        with httpx.Client(timeout=30.0) as http:
            response = http.post(self.base_url, json=data, headers=headers)
            if response.status_code in [200, 201]:
                return response.json()
            raise Exception(f"Insert failed: {response.text}")

    async def async_insert(self, data: dict, token: str = None) -> List[Dict]:
        """INSERT row(s) (async version)"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(self.base_url, json=data, headers=headers)
            response.raise_for_status()
            if response.status_code in [200, 201]:
                return response.json()
            raise Exception(f"Insert failed: {response.text}")

    def update(self, data: dict, filters: List[tuple] = None, token: str = None) -> List[Dict]:
        """UPDATE rows"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}={val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        with httpx.Client(timeout=30.0) as http:
            response = http.patch(url, json=data, headers=headers)
            if response.status_code == 200:
                return response.json()
            return []

    async def async_update(self, data: dict, filters: List[tuple] = None, token: str = None) -> List[Dict]:
        """UPDATE rows (async version)"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}={val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.patch(url, json=data, headers=headers)
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return []

    def delete(self, filters: List[tuple] = None, token: str = None) -> bool:
        """DELETE rows"""
        headers = self.client._get_headers(token)

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}=eq.{val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        with httpx.Client(timeout=30.0) as http:
            response = http.delete(url, headers=headers)
            return response.status_code in [200, 204]

    async def async_delete(self, filters: List[tuple] = None, token: str = None) -> bool:
        """DELETE rows (async version)"""
        headers = self.client._get_headers(token)

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}=eq.{val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.delete(url, headers=headers)
            response.raise_for_status()
            return response.status_code in [200, 204]


class TableOperationsAsync:
    """Async table CRUD operations via REST API"""

    def __init__(self, client: SupabaseClient, table_name: str):
        self.client = client
        self.table_name = table_name
        self.base_url = f"{client.url}/rest/v1/{table_name}"

    async def select(self, query: str = "*", token: str = None) -> List[Dict]:
        """SELECT rows"""
        url = f"{self.base_url}?select={query}"
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(url, headers=headers)
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return []

    async def insert(self, data: dict, token: str = None) -> List[Dict]:
        """INSERT row(s)"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(self.base_url, json=data, headers=headers)
            response.raise_for_status()
            if response.status_code in [200, 201]:
                return response.json()
            raise Exception(f"Insert failed: {response.text}")

    async def update(self, data: dict, filters: List[tuple] = None, token: str = None) -> List[Dict]:
        """UPDATE rows"""
        headers = self.client._get_headers(token)
        headers["Prefer"] = "return=representation"

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}={val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.patch(url, json=data, headers=headers)
            response.raise_for_status()
            if response.status_code == 200:
                return response.json()
            return []

    async def delete(self, filters: List[tuple] = None, token: str = None) -> bool:
        """DELETE rows"""
        headers = self.client._get_headers(token)

        url = self.base_url
        if filters:
            filter_str = "&".join([f"{col}=eq.{val}" for col, val in filters])
            url = f"{self.base_url}?{filter_str}"

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.delete(url, headers=headers)
            response.raise_for_status()
            return response.status_code in [200, 204]


# Global client instance
_supabase: Optional[SupabaseClient] = None

def get_supabase() -> SupabaseClient:
    global _supabase
    if _supabase is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        _supabase = SupabaseClient()
    return _supabase

def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Get current authenticated user from JWT token"""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )

    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization

    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    try:
        supabase = get_supabase()
        user_data = supabase.admin_get_user(user_id)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        return {
            "id": user_data.get("id"),
            "email": user_data.get("email"),
            "created_at": user_data.get("created_at"),
            "updated_at": user_data.get("updated_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        )


def get_user_context(
    current_user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Get user context (user_id and token) for use in other dependencies"""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )

    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization

    return {
        "user_id": current_user["id"],
        "user_token": token,
    }
