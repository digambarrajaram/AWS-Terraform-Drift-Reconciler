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


def create_pending_apply(pr_number: int, scope: str) -> bool:
    """Insert an awaiting_approval row for a newly-created PR.

    This is the PRIMARY trigger for the dashboard Approve/Reject flow:
    every drift-fix PR appears in the Approvals page as soon as it's
    created.  Dedup-guarded on (pr_number, scope) so re-runs don't
    double-insert."""
    if not _URL or not _KEY:
        print("  [pending_applies] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    try:
        existing = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=id&pr_number=eq.{pr_number}&scope=eq.{scope}&limit=1",
            headers=_HEADERS,
            timeout=10,
        )
        if existing.status_code == 200 and existing.json():
            return False  # already tracked

        resp = requests.post(
            f"{_URL}/rest/v1/{_TABLE}",
            headers=_HEADERS,
            json={
                "pr_number": pr_number,
                "scope": scope,
                "status": "awaiting_approval",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"  [pending_applies] POST failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [pending_applies] POST request failed: {exc}")
        return False


def update_pending_apply(pr_number: int, scope: str, **fields) -> bool:
    """Update the decided (approved OR rejected) pending_applies row for
    *pr_number* + *scope*.

    Terminal statuses: ``applied``, ``failed``, ``reverted_gate_blocked``,
    ``manual_revert_required``.  Returns True if a row was updated, False
    otherwise (still awaiting_approval, already terminal, or missing)."""
    if not _URL or not _KEY:
        print("  [pending_applies] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?pr_number=eq.{pr_number}&scope=eq.{scope}&status=in.(approved,rejected)",
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
