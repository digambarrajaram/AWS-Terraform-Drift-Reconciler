"""
Write scan-run lifecycle events to scan_runs in Supabase.
Raises on failure — no silent swallowing.

Set DEBUG_SCAN_RUNS=1 for verbose per-call logging.
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
_TABLE = "scan_runs"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _debug(msg: str) -> None:
    if os.environ.get("DEBUG_SCAN_RUNS"):
        print(msg)


def report_stage(run_id: str | None, stage_name: str) -> None:
    if run_id is None:
        return
    update_scan_run(run_id, current_stage=stage_name)


def create_scan_run(scope: str, unmanaged_flag: bool = False) -> str:
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
    resp = requests.post(
        f"{_URL}/rest/v1/{_TABLE}",
        headers=_HEADERS,
        json={"scope": scope, "unmanaged_flag": unmanaged_flag, "status": "running"},
        timeout=10,
    )
    _debug(f"  [scan_runs] POST → {resp.status_code} {resp.text[:120]}")
    if resp.status_code in (200, 201):
        data = resp.json()
        if isinstance(data, list) and data:
            rid = data[0]["id"]
            _debug(f"  [scan_runs] + Created run {rid}")
            return rid
    raise RuntimeError(f"scan_runs create failed ({resp.status_code}): {resp.text[:200]}")


def update_scan_run(run_id: str, **fields: Any) -> None:
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
    payload = {}
    for k, v in fields.items():
        payload[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    resp = requests.patch(
        f"{_URL}/rest/v1/{_TABLE}?id=eq.{run_id}",
        headers=_HEADERS,
        json=payload,
        timeout=10,
    )
    _debug(f"  [scan_runs] PATCH {list(fields.keys())} → {resp.status_code} {resp.text[:80]}")
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"scan_runs update failed ({resp.status_code}): {resp.text[:200]}")
    _debug(f"  [scan_runs] ✓ Updated {run_id[:8]}...")
