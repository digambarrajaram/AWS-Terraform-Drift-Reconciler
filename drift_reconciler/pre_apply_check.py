"""
Pre-apply drift gate — checks Supabase for unresolved drift findings
in the target scope before allowing terraform apply to proceed.

The Supabase rows are detection-time state, so when a plan JSON is
available in the working directory (drift-reconciler.yml generates
tfplan.json right before this step) the gate re-verifies each row
against the live plan — a resource fixed in AWS since the original
scan no longer appears in the plan and must not block the apply.
Unmanaged rows have no plan representation and always count.

Usage:
    python drift_reconciler/pre_apply_check.py <scope> [--block]

Exit codes:
    0 — no unresolved drift (or warn mode, apply proceeds regardless)
    1 — unresolved drift found and --block is set (apply blocked)
"""

import json
import sys
from pathlib import Path

from env_loader import load_env
load_env()

from drift_history import get_open_resources  # noqa: E402
from rollback_check import live_drift_rows  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python drift_reconciler/pre_apply_check.py <scope> [--block]")
        return 2

    scope = sys.argv[1]
    block = "--block" in sys.argv

    open_rows = get_open_resources(scope)

    if not open_rows:
        print(f"[pre-apply] ✓ No unresolved drift for {scope} — safe to apply.")
        return 0

    live = open_rows
    plan_path = Path("tfplan.json")
    if plan_path.is_file():
        try:
            plan_json = json.loads(plan_path.read_text(encoding="utf-8"))
            live = live_drift_rows(open_rows, plan_json)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[pre-apply] ⚠ Could not read tfplan.json ({exc}) — "
                  f"failing closed on recorded drift.")
            live = open_rows

    if not live:
        print(f"[pre-apply] ✓ Recorded drift for {scope} is resolved in the "
              f"live plan — safe to apply.")
        return 0

    print(f"[pre-apply] ⚠ Unresolved drift exists for {scope}: "
          f"{', '.join(str(r.get('resource_id')) for r in live[:5])}.")
    if block:
        print("[pre-apply] ❌ Apply BLOCKED (--block). Resolve outstanding drift first.")
        return 1
    else:
        print("[pre-apply] Apply will proceed (warn mode).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
