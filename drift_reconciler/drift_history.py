"""
Drift event log backed by Supabase (PostgreSQL via REST API).

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
No local file I/O — all reads and writes go to the remote database.

Usage (standalone test):
    python drift_reconciler/drift_history.py
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

try:
    from .env_loader import load_env
except ImportError:
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
    """Insert one row.  Returns True on success.

    Retries up to 3 attempts on transient failures (network errors and
    5xx responses).  4xx responses fail immediately — retrying won't fix
    a malformed payload or bad credentials."""
    if not _URL or not _KEY:
        print("  [history] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping")
        return False
    attempts = 0
    while True:
        attempts += 1
        try:
            resp = requests.post(
                f"{_URL}/rest/v1/{_TABLE}",
                headers=_HEADERS,
                json=row,
                timeout=10,
            )
        except requests.RequestException as exc:
            # Network error — retry unless attempts exhausted.
            if attempts >= 3:
                print(f"  ⚠⚠⚠ CRITICAL: drift_events write FAILED after {attempts} attempts for "
                      f"resource_id={row.get('resource_id', '?')} — dedup guard will NOT catch this "
                      f"resource on future scans, risking duplicate PRs. Manual DB check recommended.")
                print(f"  [history] POST request failed: {exc}")
                return False
            print(f"  [history] POST request failed (attempt {attempts}/3): {exc}")
            time.sleep(attempts)  # 1s, then 2s
            continue

        if resp.status_code in (200, 201):
            return True

        # 4xx — no point retrying a malformed payload or bad credentials.
        if 400 <= resp.status_code < 500:
            print(f"  [history] POST failed ({resp.status_code}): {resp.text[:200]}")
            return False

        # 5xx — transient server error, retry unless attempts exhausted.
        if attempts >= 3:
            print(f"  ⚠⚠⚠ CRITICAL: drift_events write FAILED after {attempts} attempts for "
                  f"resource_id={row.get('resource_id', '?')} — dedup guard will NOT catch this "
                  f"resource on future scans, risking duplicate PRs. Manual DB check recommended.")
            print(f"  [history] POST failed ({resp.status_code}): {resp.text[:200]}")
            return False
        print(f"  [history] POST failed ({resp.status_code}, attempt {attempts}/3): {resp.text[:200]}")
        time.sleep(attempts)  # 1s, then 2s
        continue


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
    trivy_passed: bool | None = None,
    trivy_summary: dict | None = None,
    rolled_back_from_pr: int | None = None,
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
        # jsonb columns take dicts/lists natively — passing json.dumps'd
        # text would store a jsonb *string*, breaking Object.entries() and
        # attribute access in every consumer (see
        # migrations/backfill_changes_jsonb_parsed.sql for the repair).
        "fields_changed": fields_changed or [],
        "drift_summary": drift_summary,
        "unmanaged": unmanaged,
        "changes_jsonb": changes_jsonb or None,
        "file_path": file_path,
        "cost_impact": cost_impact or None,
        "trivy_passed": trivy_passed,
        # trivy_summary is a plain-text blob — a jsonb string is its shape.
        "trivy_summary": json.dumps(trivy_summary) if trivy_summary else None,
        "rolled_back_from_pr": rolled_back_from_pr,
    })


def mark_reverted(pr_number: int, account: str, status: str = "reverted",
                  resolution: str = "PR rejected — AWS reverted to match original code") -> None:
    """Mark the open entry for *pr_number* as *status* (reject path: PR
    never merged; 'reverted' only when AWS was actually reverted to match
    pre-drift code, 'manual_revert_required' when a gate blocked it)."""
    ok = _patch(
        {"pr_number": pr_number, "status": "open"},
        {
            "status": status,
            "resolution": resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if ok:
        print(f"  [history] PR #{pr_number} marked {status}")
    else:
        print(f"  [history] Failed to mark PR #{pr_number} {status}")


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


def get_pr_type(pr_number: int, account: str) -> str | None:
    """Return the pr_type of the drift_events row(s) for *pr_number*, or
    None when the PR has no rows or the fetch fails.

    Used by the apply/reject dispatcher to distinguish file-only PRs
    (unmanaged / security_only — no terraform action) from drift/fix PRs
    (full gate/apply/revert path)."""
    if not _URL or not _KEY:
        return None
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=pr_type&pr_number=eq.{pr_number}&account=eq.{account}&limit=1",
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            if rows and rows[0].get("pr_type"):
                return rows[0]["pr_type"]
    except requests.RequestException:
        return None
    return None


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


def get_open_event(resource_id: str, account: str, pr_type: str | None = None) -> dict | None:
    """Return the open drift_events row for *resource_id* in *account*, or None.

    Used by the PR node to skip creating a new PR when an unresolved one
    already exists for the same resource identity + scope.

    When *pr_type* is provided the query scopes to that type so a
    security-only finding doesn't collide with an open drift-fix PR
    for the same resource, and vice versa.  When *pr_type* is None
    (the default) the query matches any type — preserving existing
    callers that don't pass it.

    Cross-checks the local ``status=open`` against the GitHub API so a PR
    that was manually closed on GitHub (without merging) isn't treated as
    still blocking — the local row stays 'open' because nothing in the
    pipeline updates it when a PR is closed externally."""
    if not _URL or not _KEY:
        return None
    filters = (
        f"&resource_id=eq.{resource_id}"
        f"&account=eq.{account}"
        f"&status=eq.open"
    )
    if pr_type is not None:
        filters += f"&pr_type=eq.{pr_type}"
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=id,pr_number,pr_type,status"
            f"{filters}"
            f"&limit=1",
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json() if resp.text else []
        if not rows:
            return None
    except requests.RequestException:
        return None

    row = rows[0]
    pr_number = row.get("pr_number")
    if not pr_number:
        return row  # no PR number — can't verify, trust local status

    # Live GitHub check — the DB status is never trusted on its own.
    # Log the state actually seen so a blocked PR is diagnosable (None =
    # no repo/token or network error; the safe default blocks).
    gh_state = _fetch_pr_state(pr_number, account)
    print(f"  [dedup] {resource_id} ({account}): DB row #{pr_number} open — "
          f"GitHub state: {gh_state!r}")
    if gh_state in (None, "open"):
        return row

    # PR is closed on GitHub — fix the stale local status so we don't
    # re-check GitHub every scan.  One PATCH, then return None so the
    # guard doesn't block a new PR.
    _patch(
        {"id": row["id"]},
        {"status": "resolved",
         "resolution": "PR closed externally — sync'd by guard on next scan"},
    )
    return None


def _fetch_pr_state(pr_number: int, account_label: str | None = None) -> str | None:
    """Return GitHub's state for PR *pr_number*: 'open', 'closed', or
    'merged' (a merged PR reports state='closed' plus merged=true).

    Resolves repo + token per-environment when *account_label* is given
    (environment row repo_url + environment_secrets.github_token).

    Returns None when the state can't be determined (no repo/token,
    network error) — callers treat None as "unknown".  A 404 (PR
    deleted) or 401 (bad token) counts as closed."""
    from drift_reconciler.github_client_utils import resolve_repo_target
    repo, token, _branch = resolve_repo_target(account_label)
    if not repo or not token:
        return None
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            return "merged" if data.get("merged") else data.get("state", "open")
        # 404 = PR doesn't exist, 401 = bad token — treat as closed
        return "closed"
    except requests.RequestException:
        return None


def _pr_is_actually_open(pr_number: int, account_label: str | None = None) -> bool:
    """Return True if GitHub PR *pr_number* is still open (not closed/merged).

    Unknown state (no repo/token, network error) is treated as open —
    the safe default: the guard must not unblock a new PR it can't verify."""
    return _fetch_pr_state(pr_number, account_label) in (None, "open")


def sync_pr_state(pr_number: int, account: str) -> str | None:
    """Reconcile the open drift_events rows for *pr_number* with the PR's
    actual GitHub state (open/closed/merged).

    Called by the GitHub webhook (closed-unmerged events) and by
    serve.py's periodic stale sweep, so a row is never left 'open' once
    GitHub has closed or merged the PR and the backend is done with it.

    Merged → rows resolve (the merged code now matches live AWS).
    Closed unmerged → rows become 'manual_revert_required' (the drift
    remains; someone must act — the same label the revert-gate path uses).

    Returns the state synced, or None when GitHub couldn't be queried
    (the row is left for the next sweep)."""
    state = _fetch_pr_state(pr_number, account)
    if state is None:
        return None
    if state == "merged":
        resolve_entry(
            pr_number, account,
            "PR merged on GitHub — state synced from GitHub",
        )
    elif state == "closed":
        mark_reverted(
            pr_number, account, status="manual_revert_required",
            resolution=("PR closed on GitHub without merging — drift "
                        "remains; manual action required"),
        )
    return state


def get_open_resources(
    account: str, except_pr_number: int | None = None
) -> list[dict]:
    """Return the open drift rows for *account* as ``{resource_id, unmanaged}``
    dicts — optionally excluding one PR's rows (the applied PR's own rows stay
    'open' until the post-apply resolve step).

    These rows are detection-time state, not current reality: callers
    (pre-apply gates) are expected to re-verify them against a live plan."""
    if not _URL or not _KEY:
        return []
    filters = f"account=eq.{account}&status=eq.open"
    if except_pr_number is not None:
        filters += f"&pr_number=neq.{except_pr_number}"
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=resource_id,unmanaged&{filters}&limit=100",
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json() if resp.text else []
            return data if isinstance(data, list) else []
        return []
    except requests.RequestException:
        return []


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
            # workflow_dispatch — no PR number.  The workflow's empty PR arg
            # collapses, so argv here is [resolve, scope, resolution].
            account = sys.argv[2]
            resolution = sys.argv[3] if len(sys.argv) >= 4 else "Manual workflow run"
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
