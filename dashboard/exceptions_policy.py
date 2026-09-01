"""Exception validation + auto-add-on-merge policy."""
from __future__ import annotations

import os
import sys
from datetime import datetime


def _requests():
    """Resolve requests via dashboard.serve so tests can patch serve.requests."""
    from dashboard import serve
    return serve.requests


def _validate_exception_entry_local(exception_type: str, entry: dict) -> tuple[bool, str | None]:
    """Same contract as ``validate_exception_entry`` in github_integration,
    replicated here so serve.py can validate without importing PyGithub."""
    if exception_type == "drift":
        addr = (entry.get("resource_address") or "").strip()
        if not addr:
            return False, "resource_address is required and must be a non-empty string."
        reason = (entry.get("reason") or "").strip()
        if not reason:
            return False, "reason is required and must be a non-empty string."
        expires = (entry.get("expires") or "").strip()
        if expires:
            try:
                exp_date = datetime.strptime(expires, "%Y-%m-%d").date()
                if exp_date <= datetime.now().date():
                    return False, f"expires ({expires}) is in the past."
            except ValueError:
                return False, f"expires ({expires}) is not a valid ISO date (YYYY-MM-DD)."
        return True, None

    elif exception_type == "security":
        addr = (entry.get("resource_address") or "").strip()
        if not addr:
            return False, "resource_address is required and must be a non-empty string."
        rule_id = (entry.get("rule_id") or "").strip()
        if not rule_id:
            return False, "rule_id is required and must be a non-empty string."
        reason = (entry.get("reason") or "").strip()
        if not reason:
            return False, "reason is required and must be a non-empty string."
        expires = (entry.get("expires") or "").strip()
        if expires:
            try:
                exp_date = datetime.strptime(expires, "%Y-%m-%d").date()
                if exp_date <= datetime.now().date():
                    return False, f"expires ({expires}) is in the past."
            except ValueError:
                return False, f"expires ({expires}) is not a valid ISO date (YYYY-MM-DD)."
        return True, None

    elif exception_type == "unmanaged":
        rt = (entry.get("resource_type") or "").strip()
        if not rt:
            return False, "resource_type is required and must be a non-empty string."
        pattern = (entry.get("resource_id_pattern") or "").strip()
        if not pattern:
            return False, "resource_id_pattern is required and must be a non-empty string."
        reason = (entry.get("reason") or "").strip()
        if not reason:
            return False, "reason is required and must be a non-empty string."
        return True, None

    return False, f"Unknown exception_type: {exception_type}"


def auto_add_exceptions_on_merge(
    pr_number: int, scope: str, pr_type: str | None, approved_by: str,
    reason: str | None = None,
) -> int:
    """Policy: merging an unmanaged/security PR auto-adds the covered
    resources/rules to the exception registry — no separate manual
    exception entry.  Fail-soft: the merge is already final on GitHub,
    so a registry write failure only means the next scan may re-flag
    (the old behavior); never fail the merge over it.

    Returns the number of exception rows successfully inserted.
    """
    import requests as _req_mod

    requests = _requests()
    inserted = 0
    if pr_type not in ("unmanaged", "security_only"):
        return 0
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("  ⚠ auto exception add skipped — Supabase not configured", file=sys.stderr)
        return 0

    read_headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    write_headers = {**read_headers, "Content-Type": "application/json"}
    base = f"{url}/rest/v1/drift_exception_registry"

    def _already_exists(exception_type: str, **filters) -> bool:
        q = "&".join(f"{k}=eq.{v}" for k, v in filters.items())
        try:
            resp = requests.get(
                f"{base}?select=id&scope=eq.{scope}&exception_type=eq.{exception_type}"
                f"&{q}&active=eq.true&limit=1",
                headers=read_headers, timeout=10,
            )
            return bool(resp.json()) if resp.text and resp.status_code == 200 else False
        except _req_mod.RequestException:
            return True  # can't check — skip the insert, fail soft

    def _insert(row: dict) -> bool:
        nonlocal inserted
        try:
            resp = requests.post(base, headers=write_headers, json=row, timeout=10)
            if resp.status_code >= 300:
                print(f"  ⚠ auto exception add failed ({resp.status_code}): "
                      f"{resp.text[:200]}", file=sys.stderr)
                return False
            inserted += 1
            return True
        except _req_mod.RequestException as exc:
            print(f"  ⚠ auto exception add failed: {exc}", file=sys.stderr)
            return False

    if pr_type == "unmanaged":
        # The unmanaged PR's drift_events rows carry the resource_ids to
        # exception.  No status filter: the merged-PR webhook resolves
        # these rows at merge time, racing this query — an exception is
        # owed for the resource regardless of its event's resolution.
        try:
            resp = requests.get(
                f"{url}/rest/v1/drift_events"
                f"?select=resource_id&pr_number=eq.{pr_number}&account=eq.{scope}"
                f"&limit=100",
                headers=read_headers, timeout=10,
            )
            rows = resp.json() if resp.text and resp.status_code == 200 else []
        except _req_mod.RequestException as exc:
            print(f"  ⚠ auto exception add: drift_events read failed: {exc}", file=sys.stderr)
            return 0
        print(f"  [exceptions] drift_events rows for pr={pr_number}: "
              f"{[r.get('resource_id') for r in rows]}", file=sys.stderr)
        for row in rows:
            rid = row.get("resource_id") or ""
            if "." not in rid:
                continue
            resource_type, pattern = rid.split(".", 1)
            if not resource_type or not pattern:
                continue
            if _already_exists("unmanaged",
                               resource_type=resource_type,
                               resource_id_pattern=pattern):
                continue
            _insert({
                "scope": scope,
                "exception_type": "unmanaged",
                "resource_type": resource_type,
                "resource_id_pattern": pattern,
                "reason": reason or f"Auto-added on merge of unmanaged PR #{pr_number}",
                "approved_by": approved_by,
                "auto": True,
            })
    elif pr_type == "security_only":
        # The (resource_address, rule_id) pairs this PR fixed were
        # recorded on its pending_applies row at scan time.
        try:
            resp = requests.get(
                f"{url}/rest/v1/pending_applies"
                f"?select=fixes_jsonb&pr_number=eq.{pr_number}&scope=eq.{scope}&limit=1",
                headers=read_headers, timeout=10,
            )
            rows = resp.json() if resp.text and resp.status_code == 200 else []
        except _req_mod.RequestException as exc:
            print(f"  ⚠ auto exception add: pending_applies read failed: {exc}", file=sys.stderr)
            return 0
        fixes = (rows[0].get("fixes_jsonb") or []) if rows else []
        if not fixes:
            print(f"  ⚠ security PR #{pr_number} has no recorded fixes_jsonb — "
                  f"skipping auto exception (add manually or re-scan)", file=sys.stderr)
            return 0
        print(f"  [exceptions] security PR #{pr_number} fixes_jsonb: "
              f"{len(fixes)} pair(s)", file=sys.stderr)
        for fix in fixes:
            ra = fix.get("resource_address") or ""
            rid = fix.get("rule_id") or ""
            if not ra or not rid:
                continue
            if _already_exists("security", resource_address=ra, rule_id=rid):
                print(f"  [exceptions] already exists: {ra} / {rid}", file=sys.stderr)
                continue
            if _insert({
                "scope": scope,
                "exception_type": "security",
                "resource_address": ra,
                "rule_id": rid,
                "reason": reason or f"Auto-added on merge of security PR #{pr_number}",
                "approved_by": approved_by,
                "auto": True,
            }):
                print(f"  [exceptions] inserted security exception: {ra} / {rid}",
                      file=sys.stderr)
    print(f"  [exceptions] auto_add done for PR #{pr_number}: "
          f"{inserted} row(s) inserted", file=sys.stderr)
    return inserted