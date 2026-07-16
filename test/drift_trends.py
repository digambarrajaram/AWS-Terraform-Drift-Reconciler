"""
Generate a drift-trends markdown report from Supabase (PostgreSQL via REST API).

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.

Usage:
    python test/drift_trends.py --account scope-a
    python test/drift_trends.py --account scope-a --days 90
"""

import argparse
import os
from datetime import datetime, timedelta, timezone
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
}


def _get(path: str) -> list[dict[str, Any]]:
    """GET rows from Supabase with the given query-string *path*.
    Returns an empty list when credentials are missing or the request fails."""
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


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def generate_report(account: str, days: int = 90) -> str:
    """Return a markdown report for *account* covering the last *days*."""
    # Build a date filter for the lookback window, applied to every query.
    date_filter = ""
    if days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        date_filter = f"&created_at=gte.{since}"

    acct_filter = f"account=eq.{account}"

    # ── Most-drifted resources ──
    most_drifted_raw = _get(
        f"select=resource_id&{acct_filter}{date_filter}"
    )
    from collections import Counter as _Counter
    most_drifted = [{"resource_id": rid, "count": cnt}
                    for rid, cnt in _Counter(r["resource_id"] for r in most_drifted_raw).most_common(15)]

    # ── MTTR by severity (fix/batch only, resolved only) ──
    mttr_raw = _get(
        f"select=severity,created_at,resolved_at&{acct_filter}{date_filter}"
        f"&status=eq.resolved&pr_type=in.(fix,batch)"
    )
    mttr: dict[str, dict[str, Any]] = {}
    if mttr_raw:
        from collections import defaultdict
        from statistics import mean

        buckets: dict[str, list[float]] = defaultdict(list)
        for row in mttr_raw:
            sev = row.get("severity", "LOW")
            ts_open = row.get("created_at", "")
            ts_done = row.get("resolved_at") or ts_open
            try:
                t0 = datetime.fromisoformat(ts_open.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(ts_done.replace("Z", "+00:00"))
                hours = (t1 - t0).total_seconds() / 3600
                buckets[sev].append(max(0, hours))
            except (ValueError, AttributeError):
                continue
        mttr = {
            sev: {"avg_hours": round(mean(h), 1), "count": len(h)}
            for sev, h in sorted(buckets.items())
        }

    # ── Rollbacks ──
    rollbacks = _get(f"select=pr_number&{acct_filter}{date_filter}&pr_type=eq.rollback")

    # ── Unresolved ──
    unresolved = _get(f"select=*&{acct_filter}{date_filter}&status=eq.open")

    # ── Total counts ──
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
            cnt = row.get("count", 0)
            lines.append(f"| `{rid}` | {cnt} |")
        lines.append("")

    if mttr:
        lines.append("## Mean Time to Remediate\n")
        lines.append("| Severity | Avg Hours | Count |")
        lines.append("|---|---|---|")
        for sev, data in mttr.items():
            lines.append(f"| {sev} | {data['avg_hours']} | {data['count']} |")
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
