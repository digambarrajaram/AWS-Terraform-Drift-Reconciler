"""
Checkpoint-2 freshness gate for rollback PRs.

Reads the stored drift baseline and compares it against the current
``terraform show -json`` plan output.  Exits 0 when every resource in
the baseline is still fresh (safe to apply).  Exits 1 when any resource
is stale (intervening change detected — abort the apply).  Exits 2 when
the baseline directory or files are missing (not a rollback PR — skip).

Usage:
    python test/rollback_check.py <pr_number> <plan_json_path>
"""

import json
import os
import sys

# Add the test directory to the path so we can import from github_integration.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from github_integration import _extract_field_values  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python test/rollback_check.py <pr_number> <plan_json_path> [--pagerduty-scope <label>]")
        return 2

    pr_number = sys.argv[1]
    plan_json_path = sys.argv[2]
    pagerduty_scope: str | None = None
    if len(sys.argv) >= 5 and sys.argv[3] == "--pagerduty-scope":
        pagerduty_scope = sys.argv[4]

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    baseline_dir = os.path.join(repo_root, ".drift-baselines", f"pr-{pr_number}")

    if not os.path.isdir(baseline_dir):
        print(f"[rollback-check] No baseline directory for PR #{pr_number} — not a rollback, skipping.")
        return 0  # not a rollback PR, nothing to gate

    with open(plan_json_path, encoding="utf-8") as f:
        plan_json = json.load(f)

    any_stale = False
    any_found = False

    for fname in sorted(os.listdir(baseline_dir)):
        if not fname.endswith(".json"):
            continue
        any_found = True

        with open(os.path.join(baseline_dir, fname), encoding="utf-8") as f:
            baseline = json.load(f)

        resource_id = baseline["resource_id"]
        changes = baseline.get("changes", {})
        fields = list(changes.keys())

        if not fields:
            print(f"  [rollback-check] {resource_id}: no fields to check — skipping")
            continue

        outcome, live_values = _extract_field_values(plan_json, resource_id, fields)

        if outcome == "not_found":
            print(
                f"  [rollback-check] {resource_id}: not found in plan — "
                f"resource may have been deleted. Treating as no-op."
            )
            continue

        if outcome == "no_diff":
            print(f"  [rollback-check] {resource_id}: already matches rollback target — nothing to apply.")
            continue

        # outcome == "present" — check each field for staleness.
        stale_fields = []
        for field in fields:
            expected = str(changes[field].get("before", ""))  # rollback target
            actual = live_values.get(field, "<missing>")
            if actual != expected:
                stale_fields.append((field, expected, actual))

        if stale_fields:
            any_stale = True
            print(f"  [rollback-check] ✗ {resource_id}: STALE — intervening change detected:")
            for field, expected, actual in stale_fields:
                print(f"      {field}: expected={expected}  actual={actual}")
        else:
            print(f"  [rollback-check] ✓ {resource_id}: freshness confirmed")

    if not any_found:
        print("[rollback-check] No baseline files found — not a rollback, skipping.")
        return 0

    if any_stale:
        print("\n[rollback-check] ❌ Rollback ABORTED — intervening changes detected during review window.")
        if pagerduty_scope:
            try:
                from pagerduty_alert import trigger_pagerduty_alert
                trigger_pagerduty_alert(
                    summary=f"Rollback aborted — intervening change for PR #{pr_number}",
                    severity="error",
                    source="terraform-drift-reconciler",
                    dedup_key=f"rollback-stale-{pr_number}",
                    account_label=pagerduty_scope,
                )
                print("[rollback-check] 📟 PagerDuty alert fired.")
            except Exception as exc:
                print(f"[rollback-check] ⚠ Failed to fire PagerDuty alert: {exc}")
        return 1

    print("\n[rollback-check] ✓ All resources fresh — safe to apply rollback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
