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
import glob, re
import os


SECURITY_RESOURCE_TYPES = (
    "aws_security_group",
    "aws_vpc_security_group_ingress_rule",
    "aws_vpc_security_group_egress_rule",
    "aws_network_acl",
    "aws_iam_policy",
    "aws_iam_role_policy",
)


def classify_security_impact(address: str, changes_dict: dict) -> str | None:
    resource_type = address.split(".")[0]
    if resource_type in SECURITY_RESOURCE_TYPES:
        return "high"
    if any(k in changes_dict for k in ("ingress", "egress")):
        return "high"
    return "low" if changes_dict else None

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
    changes = []
    keys = set((before or {}).keys()) | set((after or {}).keys())
    for key in sorted(keys):
        b_val = (before or {}).get(key)
        a_val = (after or {}).get(key)
        if normalize_tags(b_val) != normalize_tags(a_val):
            changes.append((f"{prefix}{key}", b_val, a_val))
    return changes


def normalize_tags(obj):
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "tags" and v is None:
                v = {}
            result[k] = normalize_tags(v)
        return result
    elif isinstance(obj, list):
        return [normalize_tags(v) for v in obj]
    return obj

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


def report_drift(plan, tf_dir: str = None) -> dict:
    prior_addresses = get_prior_state_addresses(plan)
    drift_entries = plan.get("resource_drift")
    used_fallback = drift_entries is None
    deleted_addresses = set()

    file_index = build_resource_file_index(tf_dir) if tf_dir else {}

    if used_fallback:
        drift_entries = [
            rc for rc in plan.get("resource_changes", [])
            if rc.get("change", {}).get("actions") == ["update"]
        ]
        deleted_externally = [
            rc for rc in plan.get("resource_changes", [])
            if rc.get("change", {}).get("actions") == ["create"]
            and rc.get("address") in prior_addresses
        ]
        deleted_addresses = {rc.get("address") for rc in deleted_externally}
        drift_entries.extend(deleted_externally)

    if not drift_entries:
        return {"report_type": "no_drift", "resources": []}

    resources = []
    for entry in drift_entries:
        address = entry.get("address")
        change = entry.get("change", {})
        actions = change.get("actions", [])
        after = change.get("after") or {}

        # Detect deletion regardless of source: explicit "delete" action,
        # OR fallback-classified, OR native resource_drift showing all-null after.
        is_deleted = (
            "delete" in actions
            or address in deleted_addresses
            or (change.get("before") and after and all(v is None for v in after.values()))
        )

        if is_deleted:
            resources.append({
                "address": address,
                "status": "deleted_externally",
                "changes": {},
                "sensitive": False,
                "security_impact": "high" if address.split(".")[0] in SECURITY_RESOURCE_TYPES else "medium",
                "file_path": file_index.get(address),
            })
            continue

        before = change.get("before", {})
        diffs = flatten_diff(before, after)
        if not diffs:
            continue

        changes_dict = {field: {"before": b, "after": a} for field, b, a in diffs}
        resources.append({
            "address": address,
            "changes": changes_dict,
            "sensitive": False,
            "security_impact": classify_security_impact(address, changes_dict),
            "file_path": file_index.get(address),
        })

    return {"report_type": "drift" if resources else "no_drift", "resources": resources}


def build_resource_file_index(tf_dir: str) -> dict:
    index = {}
    pattern = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')
    for filepath in glob.glob(f"{tf_dir}/**/*.tf", recursive=True):
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        for m in pattern.finditer(content):
            index[f"{m.group(1)}.{m.group(2)}"] = filepath
    return index


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python formatting_drift_json.py <plan.json>")
        sys.exit(1)

    plan_file = sys.argv[1]
    plan_data = load_plan(plan_file)
    result = report_drift(plan_data, tf_dir=os.path.dirname(plan_file))
    print(json.dumps(result))