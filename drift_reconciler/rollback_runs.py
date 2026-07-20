"""
Write rollback-run lifecycle events to rollback_runs in Supabase.
Follows the exact same service-role REST pattern as scan_runs.py.
"""

import json
import os
from typing import Any

import requests

try:
    from .env_loader import load_env
except ImportError:
    from env_loader import load_env
load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "rollback_runs"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _debug(msg: str) -> None:
    if os.environ.get("DEBUG_SCAN_RUNS"):
        print(msg)


def create_rollback_run(pr_number: int, scope: str, mode: str = "preview") -> str:
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
    resp = requests.post(
        f"{_URL}/rest/v1/{_TABLE}",
        headers=_HEADERS,
        json={"pr_number": pr_number, "scope": scope, "mode": mode, "status": "running"},
        timeout=10,
    )
    _debug(f"  [rollback_runs] POST → {resp.status_code} {resp.text[:120]}")
    if resp.status_code in (200, 201):
        data = resp.json()
        if isinstance(data, list) and data:
            rid = data[0]["id"]
            _debug(f"  [rollback_runs] + Created run {rid}")
            return rid
    raise RuntimeError(f"rollback_runs create failed ({resp.status_code}): {resp.text[:200]}")


def update_rollback_run(run_id: str, **fields: Any) -> None:
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
    payload = {}
    for k, v in fields.items():
        # JSONB columns: pass the dict/list directly so Supabase stores
        # it as structured JSON, not a JSON-string-inside-JSONB.
        payload[k] = v
    resp = requests.patch(
        f"{_URL}/rest/v1/{_TABLE}?id=eq.{run_id}",
        headers=_HEADERS,
        json=payload,
        timeout=10,
    )
    _debug(f"  [rollback_runs] PATCH {list(fields.keys())} → {resp.status_code} {resp.text[:80]}")
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"rollback_runs update failed ({resp.status_code}): {resp.text[:200]}")
    _debug(f"  [rollback_runs] ✓ Updated {run_id[:8]}...")
