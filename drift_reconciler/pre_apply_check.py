"""
Pre-apply drift gate — checks Supabase for unresolved drift findings
in the target scope before allowing terraform apply to proceed.

Usage:
    python drift_reconciler/pre_apply_check.py <scope> [--block]

Exit codes:
    0 — no unresolved drift (or warn mode, apply proceeds regardless)
    1 — unresolved drift found and --block is set (apply blocked)
"""

import os
import sys
from pathlib import Path

from env_loader import load_env
load_env()

from drift_history import has_unresolved_drift  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python drift_reconciler/pre_apply_check.py <scope> [--block]")
        return 2

    scope = sys.argv[1]
    block = "--block" in sys.argv

    unresolved = has_unresolved_drift(scope)

    if not unresolved:
        print(f"[pre-apply] ✓ No unresolved drift for {scope} — safe to apply.")
        return 0

    print(f"[pre-apply] ⚠ Unresolved drift exists for {scope}.")
    if block:
        print("[pre-apply] ❌ Apply BLOCKED (--block). Resolve outstanding drift first.")
        return 1
    else:
        print("[pre-apply] Apply will proceed (warn mode).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
