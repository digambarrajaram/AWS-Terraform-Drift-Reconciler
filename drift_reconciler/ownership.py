"""Resolve environment ownership for per-user isolation.

Service-role PostgREST bypasses RLS, so application code must stamp and
filter by ``user_id``. Creation paths that only know a scope (agent /
webhook) look up the owning user from ``environments``.
"""
from __future__ import annotations

import os

import requests


def owner_user_id_for_scope(scope: str, user_id: str | None = None) -> str | None:
    """Return ``environments.user_id`` for *scope* (slug).

    When *user_id* is known (authenticated dashboard paths), filter by
    ``(user_id, slug)``.  When omitted, return the owner only if exactly
    one active environment matches the slug — never the first row when
    multiple users share a slug.
    """
    if not scope:
        return None
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        return None
    try:
        query = (
            f"{base}/rest/v1/environments"
            f"?select=user_id&slug=eq.{scope}&is_active=eq.true"
        )
        if user_id:
            query += f"&user_id=eq.{user_id}&limit=1"
        else:
            query += "&limit=2"
        resp = requests.get(
            query,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json() if resp.text else []
        if user_id:
            if not rows:
                return None
            uid = rows[0].get("user_id")
            return uid if isinstance(uid, str) and uid else None
        owners = {
            r.get("user_id")
            for r in rows
            if isinstance(r.get("user_id"), str) and r.get("user_id")
        }
        if len(owners) == 1:
            return owners.pop()
        return None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None
