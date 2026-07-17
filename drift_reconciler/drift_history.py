"""
Drift event log backed by Supabase (PostgreSQL via REST API).

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
No local file I/O — all reads and writes go to the remote database.

Usage (standalone test):
    python drift_reconciler/drift_history.py
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests

from env_loader import load_env
load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "drift_events"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def _post(row: dict[str, Any]) -> bool:
    """Insert one row.  Returns True on success."""
    if not _URL or not _KEY:
        print("  [history] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    try:
        resp = requests.post(
            f"{_URL}/rest/v1/{_TABLE}",
            headers=_HEADERS,
            json=row,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"  [history] POST failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [history] POST request failed: {exc}")
        return False


def _patch(params: dict[str, Any], data: dict[str, Any]) -> bool:
    """Update matching rows.  Returns True on success."""
    if not _URL or not _KEY:
        print("  [history] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    filters = "&".join(f"{k}=eq.{v}" for k, v in params.items())
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}?{filters}",
            headers=_HEADERS,
            json=data,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        print(f"  [history] PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [history] PATCH request failed: {exc}")
        return False


def append_entry(
    *,
    resource_id: str,
    account_label: str,
    region: str,
    pr_number: int | None = None,
    pr_type: str = "fix",
    severity: str = "LOW",
    fields_changed: list[str] | None = None,
    drift_summary: str = "",
    unmanaged: bool = False,
    changes_jsonb: dict | None = None,
    file_path: str = "",
    status: str = "open",
    cost_impact: dict | None = None,
) -> None:
    """Insert a new drift event row into Supabase."""
    _post({
        "account": account_label,
        "region": region,
        "resource_id": resource_id,
        "severity": severity,
        "pr_number": pr_number,
        "pr_type": pr_type,
        "status": status,
        "fields_changed": json.dumps(fields_changed or []),
        "drift_summary": drift_summary,
        "unmanaged": unmanaged,
        "changes_jsonb": json.dumps(changes_jsonb) if changes_jsonb else None,
        "file_path": file_path,
        "cost_impact": json.dumps(cost_impact) if cost_impact else None,
    })


def resolve_entry(pr_number: int, account: str, resolution: str = "") -> None:
    """Mark the open entry for *pr_number* as resolved.

    Uses Supabase PATCH with a filter on pr_number + status=open.
    Only updates the most recent matching row (order=created_at.desc&limit=1)."""
    ok = _patch(
        {"pr_number": pr_number, "status": "open"},
        {
            "status": "resolved",
            "resolution": resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if ok:
        print(f"  [history] PR #{pr_number} resolved — {resolution}")
    else:
        print(f"  [history] Failed to resolve PR #{pr_number}")


def load_baselines(pr_number: int, account: str) -> list[dict[str, Any]]:
    """Return rollback baselines for *pr_number* from Supabase.

    Each dict has ``resource_id``, ``changes`` (the ``changes_jsonb``
    column), and ``drift_summary`` — the same shape ``_run_rollback``
    expects from the old file-based ``.drift-baselines/pr-{n}/`` reader."""
    if not _URL or not _KEY:
        print("  [history] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return []
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=resource_id,changes_jsonb,drift_summary,file_path"
            f"&pr_number=eq.{pr_number}&account=eq.{account}",
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            return [
                {
                    "resource_id": r["resource_id"],
                    "changes": json.loads(r["changes_jsonb"])
                    if isinstance(r.get("changes_jsonb"), str)
                    else r.get("changes_jsonb", {}),
                    "drift_summary": r.get("drift_summary", ""),
                    "file_path": r.get("file_path", ""),
                }
                for r in rows
                if r.get("changes_jsonb")
            ]
        print(f"  [history] load_baselines failed ({resp.status_code}): {resp.text[:200]}")
        return []
    except (requests.RequestException, json.JSONDecodeError) as exc:
        print(f"  [history] load_baselines request failed: {exc}")
        return []


def has_unresolved_drift(account: str) -> bool:
    """Return ``True`` if *account* has any open (unresolved) drift entries."""
    if not _URL or not _KEY:
        return False
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=id&account=eq.{account}&status=eq.open&limit=1",
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json() if resp.text else []
            return len(data) > 0 if isinstance(data, list) else False
        return False
    except requests.RequestException:
        return False


def log_manual_entry(account: str, resolution: str) -> None:
    """Append a standalone resolved entry for a manual workflow run
    (workflow_dispatch) where there is no PR to resolve."""
    _post({
        "account": account,
        "region": os.environ.get("AWS_REGION", "unknown"),
        "resource_id": "workflow_dispatch",
        "severity": "LOW",
        "pr_type": "manual",
        "status": "resolved",
        "resolution": resolution,
        "drift_summary": f"Manual workflow run — {resolution}",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "log-manual":
        account = sys.argv[2] if len(sys.argv) >= 3 else ""
        resolution = sys.argv[3] if len(sys.argv) >= 4 else "Manual workflow run"
        log_manual_entry(account, resolution)
    elif len(sys.argv) >= 3 and sys.argv[1] == "resolve":
        try:
            pr_number = int(sys.argv[2])
        except (ValueError, TypeError):
            # workflow_dispatch — no PR number.  Log a standalone entry.
            account = sys.argv[3] if len(sys.argv) >= 4 else sys.argv[2]
            resolution = sys.argv[4] if len(sys.argv) >= 5 else "Manual workflow run"
            print(f"  [history] No PR number — logging manual entry for {account}")
            log_manual_entry(account, resolution)
            sys.exit(0)
        account = sys.argv[3] if len(sys.argv) >= 4 else ""
        resolution = sys.argv[4] if len(sys.argv) >= 5 else ""
        resolve_entry(pr_number, account, resolution)
    elif len(sys.argv) >= 2 and sys.argv[1] == "resolve-all":
        # Backfill: mark every open entry as resolved in one query.
        account = sys.argv[2] if len(sys.argv) >= 3 else None
        resolution = sys.argv[3] if len(sys.argv) >= 4 else "Manually resolved — backfill"
        params: dict[str, Any] = {"status": "open"}
        if account:
            params["account"] = account
        ok = _patch(params, {"status": "resolved", "resolution": resolution})
        if ok:
            print(f"  [history] Backfilled open entries as resolved" +
                  (f" for {account}" if account else ""))
    else:
        # Standalone test.
        append_entry(
            resource_id="aws_instance.test",
            account_label="scope-a",
            region="us-east-1",
            pr_number=1,
            pr_type="fix",
            severity="LOW",
            fields_changed=["tags", "tags_all"],
            drift_summary="tags.Name: WebServer → WebServer123",
        )
        print("Done — check Supabase dashboard for the test row.")
