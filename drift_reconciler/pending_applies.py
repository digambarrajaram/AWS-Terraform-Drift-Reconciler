"""
Write pending_applies lifecycle events to Supabase.

Same shape as scan_runs.py / rollback_runs.py — the apply job updates its
own row by (pr_number, scope).  Updates are scoped to ``status=eq.approved``
so a re-run can't double-apply or clobber a rejected row.
"""

import os

import requests

try:
    from .env_loader import load_env
except ImportError:
    from env_loader import load_env
load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "pending_applies"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def update_pending_apply(pr_number: int, scope: str, **fields) -> bool:
    """Update the approved pending_applies row for *pr_number* + *scope*.

    Terminal statuses: ``applied``, ``failed``, ``reverted_gate_blocked``,
    ``manual_revert_required``.  Returns True if a row was updated, False
    otherwise (no approved row — already in a terminal state, rejected,
    or missing)."""
    if not _URL or not _KEY:
        print("  [pending_applies] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?pr_number=eq.{pr_number}&scope=eq.{scope}&status=eq.approved",
            headers=_HEADERS,
            json=fields,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        print(f"  [pending_applies] PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [pending_applies] PATCH request failed: {exc}")
        return False
