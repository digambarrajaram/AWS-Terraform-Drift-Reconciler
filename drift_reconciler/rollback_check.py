"""
Checkpoint-2 freshness gate for rollback PRs.

Reads the stored drift baseline from Supabase and compares it against
the current ``terraform show -json`` plan output.  Exits 0 when every
resource is still fresh (safe to apply).  Exits 1 when any resource is
stale (intervening change detected — abort the apply).  Exits 2 when
the baseline or plan is missing (not a rollback PR — skip).

Usage:
    python drift_reconciler/rollback_check.py <pr_number> <plan_json_path> [--pagerduty-scope <label>]
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

# ── Supabase write helper (service role — same pattern as drift_history) ──

def _patch_drift_events(pr_number: str, fields: dict) -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("[rollback-check] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping Supabase write")
        return False
    try:
        resp = requests.patch(
            f"{url}/rest/v1/drift_events?pr_number=eq.{pr_number}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=fields,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            print(f"  [rollback-check] ✓ Wrote freshness_gate_status to drift_events")
            return True
        print(f"  [rollback-check] ⚠ Supabase PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  [rollback-check] ⚠ Supabase request failed: {exc}")
        return False


def _record_freshness(pr_number: str, status: str) -> None:
    _patch_drift_events(pr_number, {
        "freshness_gate_status": status,
        "freshness_gate_checked_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Plan extraction (inlined to avoid PyGithub import on CI) ──

def _extract_field_values(
    plan_json: dict,
    resource_address: str,
    fields: list[str],
) -> tuple[str, dict[str, str]]:
    for rc in plan_json.get("resource_changes", []):
        if rc.get("address") != resource_address:
            continue
        change = rc.get("change", {})
        before = change.get("before", {})
        after = change.get("after", {})
        if not before:
            return ("not_found", {})
        all_same = True
        values: dict[str, str] = {}
        for field in fields:
            b_val = before.get(field)
            a_val = after.get(field)
            if b_val is not None:
                values[field] = str(b_val)
            if b_val != a_val:
                all_same = False
        if all_same and values:
            return ("no_diff", values)
        return ("present", values)
    return ("not_found", {})


# ── Main ──

def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python drift_reconciler/rollback_check.py <pr_number> <plan_json_path> [--pagerduty-scope <label>]")
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
        return 0

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

        stale_fields = []
        for field in fields:
            expected = str(changes[field].get("before", ""))
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
        _record_freshness(pr_number, "fail")
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
    _record_freshness(pr_number, "pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
