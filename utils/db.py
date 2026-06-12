"""Minimal Supabase client. Credentials come from env (GitHub Actions Secrets)."""
import os
from typing import Optional

from supabase import create_client, Client

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client
