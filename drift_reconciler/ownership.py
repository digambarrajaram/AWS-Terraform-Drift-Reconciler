"""Resolve environment ownership for per-user isolation.

Service-role PostgREST bypasses RLS, so application code must stamp and
filter by ``user_id``. Creation paths that only know a scope (agent /
webhook) look up the owning user from ``environments``.
"""
from __future__ import annotations

import os

import requests


def owner_user_id_for_scope(scope: str) -> str | None:
    """Return ``environments.user_id`` for *scope* (slug), or None if unknown."""
    if not scope:
        return None
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        return None
    try:
        resp = requests.get(
            f"{base}/rest/v1/environments"
            f"?select=user_id&slug=eq.{scope}&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json() if resp.text else []
        if not rows:
            return None
        uid = rows[0].get("user_id")
        return uid if isinstance(uid, str) and uid else None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None
