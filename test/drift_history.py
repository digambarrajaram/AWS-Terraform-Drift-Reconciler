"""
Append-only drift event log — one JSON object per line, grouped by account.

Usage (standalone test):
    python test/drift_history.py
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

# Placed under the repo root, one subdirectory per account so scope-a
# and scope-b histories are isolated and never contend for the same file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HISTORY_DIR = os.path.join(_REPO_ROOT, ".drift-history")


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
) -> None:
    """Append one drift event to the history file for *account_label*.

    The file is opened in append mode (``O_APPEND``) and the full JSON
    line + newline is written in a single ``write()`` call — atomic on
    POSIX for payloads under ``PIPE_BUF`` (4 096 bytes)."""
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": account_label,
        "region": region,
        "resource_id": resource_id,
        "severity": severity,
        "pr_number": pr_number,
        "pr_type": pr_type,
        "status": "open",
        "fields_changed": fields_changed or [],
        "drift_summary": drift_summary,
        "unmanaged": unmanaged,
    }

    account_dir = os.path.join(_HISTORY_DIR, account_label)
    os.makedirs(account_dir, exist_ok=True)

    path = os.path.join(account_dir, "drift-log.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def resolve_entry(pr_number: int, account: str, resolution: str = "") -> None:
    """Append a ``status: "resolved"`` line for *pr_number* in *account*'s
    history.  Called from the workflow after ``terraform apply`` succeeds.

    *resolution* is a human-readable label describing how the drift was
    resolved (e.g. "PR merged — code updated to match live AWS state" or
    "PR closed — AWS reverted to match original code").  It is stored in
    the resolved line for auditability.

    The guardrail parses each line as JSON rather than substring-matching
    against an assumed key order — ``json.dumps(sort_keys=True)`` in
    ``append_entry`` makes alphabetical order predictable but this function
    does not depend on it."""
    account_dir = os.path.join(_HISTORY_DIR, account)
    path = os.path.join(account_dir, "drift-log.jsonl")

    if not os.path.isfile(path):
        print(f"  [history] No history file at {path} — skipping resolve for PR #{pr_number}")
        return

    found = False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("pr_number") == pr_number and entry.get("status") == "open":
                found = True
                break

    if not found:
        print(f"  [history] No open entry for PR #{pr_number} in {account} — skipping resolve")
        return

    resolved = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "pr_number": pr_number,
        "status": "resolved",
        "resolution": resolution,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(resolved, sort_keys=True) + "\n")
    print(f"  [history] PR #{pr_number} resolved — {resolution}")


# ---------------------------------------------------------------------------
# CLI — supports standalone resolve for workflow use
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "resolve":
        resolution = sys.argv[4] if len(sys.argv) >= 5 else ""
        resolve_entry(int(sys.argv[2]), sys.argv[3], resolution)
    elif len(sys.argv) >= 3 and sys.argv[1] == "resolve-all":
        # Backfill: mark every open entry for the given account as resolved.
        account = sys.argv[2]
        resolution = sys.argv[3] if len(sys.argv) >= 4 else "Manually resolved — backfill"
        account_dir = os.path.join(_HISTORY_DIR, account)
        path = os.path.join(account_dir, "drift-log.jsonl")
        if not os.path.isfile(path):
            print(f"  [history] No history file for {account}")
            sys.exit(0)
        resolved_count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("status") == "open":
                    pr = entry.get("pr_number", "?")
                    resolve_entry(pr, account, resolution)
                    resolved_count += 1
        print(f"  [history] Backfilled {resolved_count} open entry(s) as resolved in {account}")
    else:
        # Standalone test — write a sample entry.
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
        account_dir = os.path.join(_HISTORY_DIR, "scope-a")
        path = os.path.join(account_dir, "drift-log.jsonl")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                print(f"Wrote: {f.readline().strip()}")
        else:
            print("File not created — check permissions")
if __name__ == "__main__":
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
    account_dir = os.path.join(_HISTORY_DIR, "scope-a")
    path = os.path.join(account_dir, "drift-log.jsonl")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            print(f"Wrote: {f.readline().strip()}")
    else:
        print("File not created — check permissions")
