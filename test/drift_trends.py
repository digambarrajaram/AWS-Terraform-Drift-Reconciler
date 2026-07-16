"""
Generate a drift-trends markdown report from the per-account history logs.

Usage:
    python test/drift_trends.py --account scope-a
    python test/drift_trends.py --account scope-a --days 90
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HISTORY_DIR = os.path.join(_REPO_ROOT, ".drift-history")


def _load_history(account: str) -> list[dict[str, Any]]:
    """Read the history file for *account* and merge open + resolved
    lines by ``pr_number``.  Descriptive fields come from the first
    (open) line; ``status`` and ``resolved_at`` are overwritten by
    any later (resolved) line."""
    path = os.path.join(_HISTORY_DIR, account, "drift-log.jsonl")
    if not os.path.isfile(path):
        return []

    by_pr: dict[int, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            pr = entry.get("pr_number")
            if pr is None:
                continue
            if pr not in by_pr:
                by_pr[pr] = entry
            else:
                by_pr[pr].update(entry)

    return list(by_pr.values())


def _compute_mttr(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mean time to remediate by severity, for fix/batch PRs only.
    Rollbacks are excluded — undoing a fix isn't remediating new drift."""
    from datetime import datetime, timezone

    buckets: dict[str, list[float]] = defaultdict(list)
    for e in entries:
        if e.get("pr_type") not in ("fix", "batch"):
            continue
        if e.get("status") != "resolved":
            continue
        ts_open = e.get("timestamp")
        ts_done = e.get("resolved_at") or e.get("timestamp")
        if not ts_open:
            continue
        try:
            t0 = datetime.fromisoformat(ts_open.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(ts_done.replace("Z", "+00:00"))
            hours = (t1 - t0).total_seconds() / 3600
        except (ValueError, AttributeError):
            continue
        sev = e.get("severity", "LOW")
        buckets[sev].append(max(0, hours))

    return {
        sev: {
            "avg_hours": round(mean(hours), 1),
            "count": len(hours),
        }
        for sev, hours in sorted(buckets.items())
    }


def generate_report(account: str, days: int = 90) -> str:
    """Return a markdown report for *account* covering the last *days*."""
    all_entries = _load_history(account)
    if not all_entries:
        return f"# Drift Trends — {account}\n\n_No history data available._\n"

    cutoff = None
    if days > 0:
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        all_entries = [
            e for e in all_entries
            if e.get("timestamp", "") >= cutoff.isoformat()
        ]

    lines: list[str] = []
    lines.append(f"# Drift Trends — {account}")
    if days > 0:
        lines.append(f"_Last {days} days.  Generated {_now_str()}._\n")
    else:
        lines.append(f"_All-time.  Generated {_now_str()}._\n")

    # ── Most-drifted resources ──
    counter: Counter = Counter()
    for e in all_entries:
        counter[e.get("resource_id", "?")] += 1
    if counter:
        lines.append("## Most Drifted Resources\n")
        lines.append("| Resource | Drifts |")
        lines.append("|---|---|")
        for rid, count in counter.most_common(15):
            lines.append(f"| `{rid}` | {count} |")
        lines.append("")

    # ── MTTR by severity ──
    mttr = _compute_mttr(all_entries)
    if mttr:
        lines.append("## Mean Time to Remediate\n")
        lines.append("| Severity | Avg Hours | Count |")
        lines.append("|---|---|---|")
        for sev, data in mttr.items():
            lines.append(f"| {sev} | {data['avg_hours']} | {data['count']} |")
        lines.append("")

    # ── Rollbacks ──
    rollbacks = [e for e in all_entries if e.get("pr_type") == "rollback"]
    if rollbacks:
        lines.append("## Rollbacks\n")
        lines.append(f"| Count |")
        lines.append("|---|")
        lines.append(f"| {len(rollbacks)} |")
        lines.append("")

    # ── Unresolved ──
    unresolved = [e for e in all_entries if e.get("status") != "resolved"]
    if unresolved:
        lines.append("## Unresolved\n")
        lines.append("| Resource | Account | Severity | Detected |")
        lines.append("|---|---|---|---|")
        for e in unresolved:
            ts = e.get("timestamp", "?")[:10]
            lines.append(
                f"| `{e.get('resource_id', '?')}` "
                f"| {e.get('account', '?')} "
                f"| {e.get('severity', '?')} "
                f"| {ts} |"
            )
        lines.append("")

    # ── Summary ──
    total = len(all_entries)
    unique = len(set(e.get("resource_id", "") for e in all_entries))
    resolved = total - len(unresolved)
    lines.append("## Summary\n")
    lines.append(f"- **Total drifts:** {total}")
    lines.append(f"- **Unique resources:** {unique}")
    lines.append(f"- **Resolved:** {resolved}")
    lines.append(f"- **Unresolved:** {len(unresolved)}")
    lines.append("")

    return "\n".join(lines)


def _now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a drift-trends markdown report."
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
