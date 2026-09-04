"""
Write pending_applies lifecycle events to Supabase.

Same shape as scan_runs.py / rollback_runs.py — the apply job updates its
own row by (pr_number, scope).  Updates are scoped to ``status=eq.approved``
so a re-run can't double-apply or clobber a rejected row.
"""

import os
from datetime import datetime, timezone

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


def decision_claim_miss_error(row: dict | None) -> tuple[int, str, dict]:
    """Map a failed awaiting_approval claim to (http_status, message, extra).

    Used when the compare-and-set PATCH matched zero rows — either the id
    is gone, or the row already left awaiting_approval (duplicate click /
    concurrent decision).
    """
    if not row:
        return 404, "Pending apply not found.", {}
    current = row.get("status") or "unknown"
    if current == "awaiting_approval":
        # Claim lost a race that somehow left the row pending — client should retry.
        return 409, "Could not claim this approval — please retry.", {"current_status": current}
    return (
        409,
        f"Already handled — this PR is currently '{current}'.",
        {"current_status": current},
    )


def claim_decision(pending_id: str, decision: str, approved_by: str) -> dict:
    """Atomically claim ``awaiting_approval`` → ``approved``|``rejected``|``excepted``.

    Returns ``{"ok": True, "row": ...}`` on success, or
    ``{"ok": False, "http_status": N, "error": "...", ...}`` on failure.
    """
    if decision not in ("approved", "rejected", "excepted"):
        return {"ok": False, "http_status": 400, "error": "decision must be 'approved', 'rejected', or 'excepted'."}
    if not (approved_by or "").strip():
        return {"ok": False, "http_status": 400, "error": "approved_by is required."}
    if not _URL or not _KEY:
        return {"ok": False, "http_status": 502, "error": "Supabase not configured"}

    headers = {
        **_HEADERS,
        "Prefer": "return=representation",
    }
    table_url = f"{_URL}/rest/v1/{_TABLE}"
    payload = {
        "status": decision,
        "approved_by": approved_by.strip(),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.patch(
            f"{table_url}?id=eq.{pending_id}&status=eq.awaiting_approval",
            headers=headers,
            json=payload,
            timeout=10,
        )
        claimed = resp.json() if resp.text and resp.status_code == 200 else []
        if isinstance(claimed, dict):
            claimed = [claimed]
        if claimed:
            return {"ok": True, "row": claimed[0]}

        # Zero rows claimed — look up current status for a clear conflict message.
        lookup = requests.get(
            f"{table_url}?select=*&id=eq.{pending_id}&limit=1",
            headers=headers,
            timeout=10,
        )
        rows = lookup.json() if lookup.text and lookup.status_code == 200 else []
        if isinstance(rows, dict):
            rows = [rows]
        status, message, extra = decision_claim_miss_error(rows[0] if rows else None)
        out = {"ok": False, "http_status": status, "error": message}
        out.update(extra)
        return out
    except requests.RequestException as exc:
        return {"ok": False, "http_status": 502, "error": f"Supabase unreachable: {exc}"}


def create_pending_apply(pr_number: int, scope: str, pr_type: str | None = None,
                         review_only: bool = False, user_id: str | None = None) -> bool:
    """Insert an awaiting_approval row for a newly-created PR.

    This is the PRIMARY trigger for the dashboard Approve/Reject flow:
    every PR appears in the Approvals page as soon as it's created.
    Dedup-guarded on (pr_number, scope) so re-runs don't double-insert.
    *pr_type* mirrors the drift_events vocabulary (fix/batch/unmanaged/
    security_only/rollback) so the queue can label and filter PR kinds.
    *review_only* marks security PRs that carry no .tf patch (manual
    review) — Approvals treats them differently from real-fix PRs.

    *user_id* is stamped from the owning environment when omitted (agent /
    webhook paths have no JWT — option (a) via environment owner lookup).
    """
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

        if user_id is None:
            from drift_reconciler.ownership import owner_user_id_for_scope
            user_id = owner_user_id_for_scope(scope)
        row = {
            "pr_number": pr_number,
            "scope": scope,
            "status": "awaiting_approval",
            "pr_type": pr_type,
            "review_only": bool(review_only),
        }
        if user_id:
            row["user_id"] = user_id

        resp = requests.post(
            f"{_URL}/rest/v1/{_TABLE}",
            headers=_HEADERS,
            json=row,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"  [pending_applies] POST failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [pending_applies] POST request failed: {exc}")
        return False


def set_security_fixes(pr_number: int, scope: str, fixes: list[dict]) -> bool:
    """Persist the (resource_address, rule_id) pairs a security PR fixes
    onto its awaiting_approval row.  The merge handler reads these back
    to auto-add security exceptions — future scans skip already-fixed
    findings without a separate manual exception entry."""
    if not _URL or not _KEY:
        print("  [pending_applies] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?pr_number=eq.{pr_number}&scope=eq.{scope}&status=eq.awaiting_approval",
            headers=_HEADERS,
            json={"fixes_jsonb": fixes},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        print(f"  [pending_applies] fixes PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [pending_applies] fixes PATCH request failed: {exc}")
        return False


def update_pending_apply(pr_number: int, scope: str, **fields) -> bool:
    """Update the decided (approved OR rejected) pending_applies row for
    *pr_number* + *scope*.

    Terminal statuses: ``applied``, ``failed``, ``reverted`` (file-only
    revert), ``reverted_gate_blocked``, ``manual_revert_required``.
    Returns True if a row was updated, False otherwise (still
    awaiting_approval, already terminal, or missing)."""
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
