"""
Parse a `terraform show -json <planfile>` output and report drift.

Usage:
    python formatting_drift_json.py plan.json

Reads the `resource_drift` array (pure drift: live infra vs. state file),
falling back to `resource_changes` (filtered to non no-op) if resource_drift
isn't present in this Terraform version's output.
"""

import json
import sys


def load_plan(path):
    # Read raw bytes first, then detect encoding — PowerShell's `>` redirect
    # often writes UTF-16 (with a FF FE or FE FF BOM) instead of UTF-8.
    with open(path, "rb") as f:
        raw = f.read()

    if not raw.strip():
        raise ValueError(f"{path} is empty. Re-run: terraform show -json <planfile> > {path}")

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8")

    text = text.strip()
    if not text:
        raise ValueError(f"{path} decoded to empty content.")

    return json.loads(text)


def flatten_diff(before, after, prefix=""):
    """Yield (field, before_value, after_value) for any top-level keys that differ."""
    changes = []
    keys = set((before or {}).keys()) | set((after or {}).keys())
    for key in sorted(keys):
        b_val = (before or {}).get(key)
        a_val = (after or {}).get(key)
        if b_val != a_val:
            changes.append((f"{prefix}{key}", b_val, a_val))
    return changes


def get_prior_state_addresses(plan):
    """
    Collect every resource address Terraform currently tracks in state
    (walks root_module + all nested child_modules). Used to tell apart
    'create' actions that mean deleted-externally (address was already
    in state) from create actions that mean never-created (address is new).
    """
    addresses = set()

    def walk(module):
        for res in module.get("resources", []):
            addresses.add(res["address"])
        for child in module.get("child_modules", []):
            walk(child)

    root_module = plan.get("prior_state", {}).get("values", {}).get("root_module", {})
    if root_module:
        walk(root_module)
    return addresses


def report_drift(plan) -> dict:
    drift_entries = plan.get("resource_drift")
    used_fallback = drift_entries is None

    if used_fallback:
        prior_addresses = get_prior_state_addresses(plan)
        drift_entries = [
            rc for rc in plan.get("resource_changes", [])
            if rc.get("change", {}).get("actions") == ["update"]
        ]
        deleted_externally = [
            rc for rc in plan.get("resource_changes", [])
            if rc.get("change", {}).get("actions") == ["create"]
            and rc.get("address") in prior_addresses
        ]
        drift_entries.extend(deleted_externally)

    if not drift_entries:
        return {"report_type": "no_drift", "resources": []}

    resources = []
    for entry in drift_entries:
        change = entry.get("change", {})
        before = change.get("before", {})
        after = change.get("after", {})
        diffs = flatten_diff(before, after)

        changes_dict = {field: {"before": b, "after": a} for field, b, a in diffs}

        # crude security heuristic — adjust to your needs
        security_impact = None
        if any(k in changes_dict for k in ("ingress", "egress")):
            security_impact = "high"
        elif changes_dict:
            security_impact = "low"

        resources.append({
            "address": entry.get("address"),
            "changes": changes_dict,
            "sensitive": False,
            "security_impact": security_impact,
        })

    return {"report_type": "drift", "resources": resources}
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python formatting_drift_json.py <plan.json>")
        sys.exit(1)

    plan_file = sys.argv[1]
    plan_data = load_plan(plan_file)
    result = report_drift(plan_data)
    print(json.dumps(result))  