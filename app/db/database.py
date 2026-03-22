from supabase import create_client, Client
from ..core.config import settings

supabase: Client = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment")
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return supabase

def get_service_client() -> Client:
    """Admin client with service role key - bypasses RLS"""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
