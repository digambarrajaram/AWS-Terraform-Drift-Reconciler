"""
Generate a drift-trends markdown report from Supabase (PostgreSQL via REST API).

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) in the
environment.  Aggregations are pushed to server-side RPC functions; the Python
layer only formats the results.

Usage:
    python drift_reconciler/drift_trends.py --account scope-a
    python drift_reconciler/drift_trends.py --account scope-a --days 90
"""

import argparse
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
}


def _get(path: str) -> list[dict[str, Any]]:
    """GET rows from the drift_events table.  Returns [] on failure."""
    if not _URL or not _KEY:
        print("  [trends] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return []
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}?{path}",
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json() if resp.text else []
        print(f"  [trends] GET failed ({resp.status_code}): {resp.text[:200]}")
        return []
    except requests.RequestException as exc:
        print(f"  [trends] GET request failed: {exc}")
        return []


def _rpc(fn: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Call a Supabase RPC function.  Returns [] on failure."""
    if not _URL or not _KEY:
        print("  [trends] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return []
    try:
        resp = requests.post(
            f"{_URL}/rest/v1/rpc/{fn}",
            headers={**_HEADERS, "Content-Type": "application/json"},
            json=params,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json() if resp.text else []
        print(f"  [trends] RPC {fn} failed ({resp.status_code}): {resp.text[:200]}")
        return []
    except requests.RequestException as exc:
        print(f"  [trends] RPC {fn} request failed: {exc}")
        return []


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def generate_report(account: str, days: int = 90) -> str:
    """Return a markdown report for *account* covering the last *days*."""
    rpc_params = {"p_account": account, "p_days": days}

    # Date filter for raw _get queries (rollbacks, unresolved, totals).
    date_filter = ""
    if days > 0:
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        date_filter = f"&created_at=gte.{since}"
    acct_filter = f"account=eq.{account}"

    # ── Most-drifted resources (RPC) ──
    most_drifted = _rpc("get_most_drifted", rpc_params)

    # ── MTTR by severity (RPC) ──
    mttr_rows = _rpc("get_mttr_by_severity", rpc_params)
    mttr: dict[str, dict[str, Any]] = {}
    if mttr_rows:
        for row in mttr_rows:
            sev = row.get("severity", "LOW")
            mttr[sev] = {"avg_hours": row.get("avg_hours", 0), "count": row.get("count", 0)}

    # ── Drift volume over time (RPC) ──
    volume_rows = _rpc("get_drift_volume_daily", rpc_params)

    # ── Rollbacks (raw query — no RPC yet) ──
    rollbacks = _get(f"select=pr_number&{acct_filter}{date_filter}&pr_type=eq.rollback")

    # ── Unresolved (raw query — no RPC yet) ──
    unresolved = _get(f"select=*&{acct_filter}{date_filter}&status=eq.open")

    # ── Total counts (raw query) ──
    total_raw = _get(f"select=resource_id&{acct_filter}{date_filter}")
    total = len(total_raw)
    unique = len(set(r.get("resource_id", "") for r in total_raw))

    # ── Build report ──
    lines: list[str] = []
    lines.append(f"# Drift Trends — {account}")
    if days > 0:
        lines.append(f"_Last {days} days.  Generated {_now_str()}._\n")
    else:
        lines.append(f"_All-time.  Generated {_now_str()}._\n")

    if most_drifted:
        lines.append("## Most Drifted Resources\n")
        lines.append("| Resource | Drifts |")
        lines.append("|---|---|")
        for row in most_drifted:
            rid = row.get("resource_id", "?")
            cnt = row.get("drift_count", 0)
            lines.append(f"| `{rid}` | {cnt} |")
        lines.append("")

    if mttr:
        lines.append("## Mean Time to Remediate\n")
        lines.append("| Severity | Avg Hours | Count |")
        lines.append("|---|---|---|")
        for sev in sorted(mttr):
            data = mttr[sev]
            lines.append(f"| {sev} | {data['avg_hours']} | {data['count']} |")
        lines.append("")

    if volume_rows:
        lines.append("## Drift Volume (Daily)\n")
        lines.append("| Date | Events |")
        lines.append("|---|---|")
        for row in volume_rows:
            day = str(row.get("day", "?"))[:10]
            cnt = row.get("count", 0)
            lines.append(f"| {day} | {cnt} |")
        lines.append("")

    if rollbacks:
        lines.append("## Rollbacks\n")
        lines.append(f"| Count |")
        lines.append("|---|")
        lines.append(f"| {len(rollbacks)} |")
        lines.append("")

    if unresolved:
        lines.append("## Unresolved\n")
        lines.append("| Resource | Account | Severity | Detected |")
        lines.append("|---|---|---|---|")
        for row in unresolved:
            ts = (row.get("created_at", "?") or "?")[:10]
            lines.append(
                f"| `{row.get('resource_id', '?')}` "
                f"| {row.get('account', '?')} "
                f"| {row.get('severity', '?')} "
                f"| {ts} |"
            )
        lines.append("")

    lines.append("## Summary\n")
    lines.append(f"- **Total drifts:** {total}")
    lines.append(f"- **Unique resources:** {unique}")
    lines.append(f"- **Resolved:** {total - len(unresolved)}")
    lines.append(f"- **Unresolved:** {len(unresolved)}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a drift-trends markdown report from Supabase."
    )
    parser.add_argument("--account", default="scope-a", help="Account label to report on")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (0 = all-time)")
    parser.add_argument("--output", default=None, help="Write to file instead of stdout")
    args = parser.parse_args()

    report = generate_report(args.account, args.days)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)
