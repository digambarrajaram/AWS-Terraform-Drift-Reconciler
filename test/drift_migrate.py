"""
One-shot: migrate local .drift-history/*.jsonl files into Supabase.

Reads every JSONL line, POSTs each as a row to the drift_events table.
Skips lines that are already in Supabase (matched by pr_number + account).

Usage:
    python test/drift_migrate.py --account scope-a
    python test/drift_migrate.py --account scope-b
    python test/drift_migrate.py --all        # migrate every account
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

# Zero-dependency .env loader — same pattern as drift_history.py.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.is_file():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            if _k.strip() not in os.environ:
                os.environ[_k.strip()] = _v.strip()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "drift_events"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HISTORY_DIR = _REPO_ROOT / ".drift-history"


def _post(row: dict[str, Any]) -> bool:
    if not _URL or not _KEY:
        print("  SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return False
    try:
        resp = requests.post(
            f"{_URL}/rest/v1/{_TABLE}",
            headers=_HEADERS,
            json=row,
            timeout=10,
        )
        if resp.status_code in (200, 201, 409):
            return True
        print(f"  POST failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  POST failed: {exc}")
        return False


def _already_exists(pr_number: int, account: str) -> bool:
    """Check whether a row for this PR+account already exists in Supabase."""
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}"
            f"?select=id&pr_number=eq.{pr_number}&account=eq.{account}",
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return len(data) > 0 if isinstance(data, list) else False
        return False
    except requests.RequestException:
        return False


def _patch(params: dict[str, Any], data: dict[str, Any]) -> bool:
    """Update matching rows.  Returns True on success."""
    if not _URL or not _KEY:
        print("  SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
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
        print(f"  PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  PATCH request failed: {exc}")
        return False


def migrate_account(account: str) -> int:
    path = _HISTORY_DIR / account / "drift-log.jsonl"
    if not path.is_file():
        print(f"  No local history file for {account} — skipping")
        return 0

    count = 0
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"  Skipping unparseable line: {line[:80]}")
                continue

            pr = entry.get("pr_number")
            if pr and _already_exists(pr, account):
                skipped += 1
                continue

            row = {
                "account": entry.get("account", account),
                "region": entry.get("region", ""),
                "resource_id": entry.get("resource_id", ""),
                "severity": entry.get("severity", "LOW"),
                "pr_number": pr,
                "pr_type": entry.get("pr_type", "fix"),
                "status": entry.get("status", "open"),
                "fields_changed": json.dumps(entry.get("fields_changed", [])),
                "drift_summary": entry.get("drift_summary", ""),
                "unmanaged": entry.get("unmanaged", False),
                "resolution": entry.get("resolution"),
                "resolved_at": entry.get("timestamp") if entry.get("status") == "resolved" else None,
            }
            if _post(row):
                count += 1
            else:
                print(f"  Failed to migrate PR #{pr}")

    print(f"  {account}: migrated {count}, skipped {skipped} (already in Supabase)")
    return count


def migrate_baselines(repo_root: Path) -> int:
    """Read .drift-baselines/pr-*/ and UPDATE the matching Supabase row
    with changes_jsonb from the baseline JSON files."""
    baseline_dir = repo_root / ".drift-baselines"
    if not baseline_dir.is_dir():
        print("  No .drift-baselines directory — nothing to migrate")
        return 0

    count = 0
    for pr_dir in sorted(baseline_dir.iterdir()):
        if not pr_dir.is_dir():
            continue
        pr_number = pr_dir.name.replace("pr-", "")
        if not pr_number.isdigit():
            continue
        pr_number_int = int(pr_number)

        for bf in sorted(pr_dir.glob("*.json")):
            try:
                baseline = json.loads(bf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            rid = baseline.get("resource_id", "")
            changes = baseline.get("changes")
            if not rid or not changes:
                continue

            ok = _patch(
                {"pr_number": pr_number_int, "resource_id": rid},
                {"changes_jsonb": json.dumps(changes)},
            )
            if ok:
                count += 1
            else:
                print(f"  Failed to migrate baseline for {rid} (PR #{pr_number_int})")

    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate local files to Supabase."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account", help="Migrate .drift-history for a single account")
    group.add_argument("--all", action="store_true", help="Migrate .drift-history for all accounts")
    group.add_argument("--baselines", action="store_true", help="Migrate .drift-baselines to changes_jsonb")
    args = parser.parse_args()
    args = parser.parse_args()

    if args.baselines:
        total = migrate_baselines(_REPO_ROOT)
        print(f"\nTotal baseline files migrated: {total}")
    elif args.all:
        if not _HISTORY_DIR.is_dir():
            print("No .drift-history directory found — nothing to migrate")
            sys.exit(0)
        total = 0
        for d in sorted(_HISTORY_DIR.iterdir()):
            if d.is_dir():
                total += migrate_account(d.name)
        print(f"\nTotal migrated: {total}")
    else:
        migrate_account(args.account)
