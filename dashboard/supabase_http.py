"""Thin Supabase REST helpers shared by dashboard handlers."""
import os

import requests


def _supabase_headers():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _supabase_get(path, params=None):
    url = f"{os.environ.get('SUPABASE_URL', '').rstrip('/')}/rest/v1/{path}"
    return requests.get(url, headers=_supabase_headers(), params=params, timeout=10)
