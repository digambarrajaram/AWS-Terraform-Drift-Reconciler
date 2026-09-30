"""
Parse a `terraform show -json <planfile>` output and report drift.

Usage:
    python formatting_drift_json.py plan.json

Reads the ``resource_drift`` array (pure drift: prior state → refreshed live)
and unions it with actionable ``resource_changes``. Findings always use
**drift convention**: ``before`` = IaC/config value, ``after`` = live AWS.

Terraform ``resource_changes`` is the opposite (before=live, after=config),
so those entries are normalized (swapped) before building the report.

Supports a **drift-exceptions registry** (``drift-exceptions.json`` in the
terraform root directory) for suppressing known/accepted drift at scale
without touching the IaC files.  See the registry file for the schema.
"""

import json
import sys
import glob, re
import os
from datetime import date

import requests

# Invoked as ``python drift_reconciler/formatting_drift_json.py`` from terraform_ops;
# only the script directory is on sys.path unless the repo root is added.
if __package__ in (None, ""):
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from drift_reconciler.drift_baseline import plan_field_values_equal


# Provider-computed attributes that appear in plan JSON but must not be
# written into HCL (terraform validate rejects them).
_READ_ONLY_DRIFT_ATTRS = frozenset({"tags_all"})

SECURITY_RESOURCE_TYPES = (
    "aws_security_group",
    "aws_vpc_security_group_ingress_rule",
    "aws_vpc_security_group_egress_rule",
    "aws_network_acl",
    "aws_iam_policy",
    "aws_iam_role_policy",
)

# External deletion of these types is treated as high security impact.
HIGH_IMPACT_DELETION_TYPES = (
    "aws_lambda_function",
    "aws_instance",
    "aws_rds_cluster",
    "aws_db_instance",
)


def _resource_type_from_address(address: str) -> str:
    """Return the Terraform resource type from an address (module-safe)."""
    if not address:
        return ""
    bare = re.sub(r"\[.*?\]$", "", address)
    parts = bare.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def classify_security_impact(address: str, changes_dict: dict) -> str | None:
    resource_type = _resource_type_from_address(address)
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
    # Terraform resource before/after are objects; tolerate null / odd shapes
    # so a single bad entry cannot abort the whole report.
    if before is None and after is None:
        return []
    if not isinstance(before, dict) or not isinstance(after, dict):
        if before != after and normalize_tags(before) != normalize_tags(after):
            label = prefix.rstrip(".") if prefix else "value"
            return [(label or "value", before, after)]
        return []
    changes = []
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys):
        b_val = before.get(key)
        a_val = after.get(key)
        if not plan_field_values_equal(f"{prefix}{key}", b_val, a_val):
            if normalize_tags(b_val) == normalize_tags(a_val):
                continue
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


def load_drift_exceptions(scope: str) -> tuple[list[dict], list[dict]]:
    """Load drift exception entries for *scope* from Supabase.

    Returns (active_exceptions, expired_exceptions).  Expired entries
    are returned separately so the caller can warn about them; they are
    NOT applied as suppressions.
    """
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key or not scope:
        return [], []

    try:
        resp = requests.get(
            f"{url}/rest/v1/drift_exception_registry"
            f"?select=resource_address,drift_type,reason,approved_by,expires,auto"
            f"&scope=eq.{scope}&exception_type=eq.drift&active=eq.true",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return [], []
        entries = resp.json() if resp.text else []
    except requests.RequestException:
        return [], []

    if not isinstance(entries, list):
        return [], []

    today = date.today()
    active, expired = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        expires_str = (entry.get("expires") or "").strip()
        if expires_str:
            try:
                expires_date = date.fromisoformat(expires_str)
                if expires_date <= today:
                    expired.append(entry)
                    continue
            except (ValueError, TypeError):
                pass  # malformed date — treat as active
        active.append(entry)
    return active, expired


def check_security_suppression(
    resource_address: str, rule_id: str, scope: str
) -> dict | None:
    """Return the active security exception row for this
    (resource_address, rule_id) pair, or ``None`` if none applies.

    Fails open — any network error or missing configuration returns None
    (no suppression), so a broken suppression check never silently hides a
    real security finding."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key or not scope:
        return None

    try:
        resp = requests.get(
            f"{url}/rest/v1/drift_exception_registry"
            f"?select=id,expires,approved_by,created_at,reason"
            f"&scope=eq.{scope}"
            f"&exception_type=eq.security"
            f"&resource_address=eq.{resource_address}"
            f"&rule_id=eq.{rule_id}"
            f"&active=eq.true"
            f"&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json() if resp.text else []
    except requests.RequestException:
        return None

    if not isinstance(rows, list) or not rows:
        return None

    entry = rows[0]
    if not isinstance(entry, dict):
        return None

    expires_str = (entry.get("expires") or "").strip()
    if not expires_str:
        return entry  # no expiry — permanently suppressed

    try:
        expires_date = date.fromisoformat(expires_str)
        if expires_date <= date.today():
            return None  # expired — don't honor
        return entry  # future expiry — still active
    except (ValueError, TypeError):
        return None  # malformed date — can't confirm validity, fail open


def _matches_exception(resource_address: str, drift_fields: set[str], status: str | None, entry: dict) -> bool:
    """Return True if *entry* suppresses this specific drift finding."""
    addr = entry.get("resource_address", "")
    if not addr:
        return False

    # resource_address can be an exact match or a prefix
    if resource_address == addr:
        pass
    elif addr.endswith(".") and resource_address.startswith(addr):
        pass
    else:
        return False

    dtype = entry.get("drift_type", "*")
    if dtype == "*" or dtype == status:
        return True
    if dtype in drift_fields:
        return True
    return False


def apply_drift_exceptions(
    resources: list[dict], exceptions: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Split *resources* into (suppressed, remaining)."""
    if not exceptions:
        return [], resources

    suppressed, remaining = [], []
    for r in resources:
        fields = set(r.get("changes", {}).keys())
        status = r.get("status")
        matched = None
        for exc in exceptions:
            if _matches_exception(r.get("address", ""), fields, status, exc):
                matched = exc
                break
        if matched:
            r["_suppressed_by"] = matched
            suppressed.append(r)
        else:
            remaining.append(r)
    return suppressed, remaining


def _entries_from_resource_changes(plan, prior_addresses: set) -> tuple[list, set]:
    """Build drift-candidate entries from resource_changes + deleted-externally set.

    Includes updates, replaces, and creates that re-create a still-tracked
    address (resource deleted outside Terraform). Skips pure pending creates
    (address never in state) and no-ops.
    """
    entries = []
    deleted_addresses = set()
    for rc in plan.get("resource_changes", []) or []:
        if rc.get("mode") == "data":
            continue
        actions = rc.get("change", {}).get("actions") or []
        address = rc.get("address")
        if not address or not actions or actions == ["no-op"] or actions == ["read"]:
            continue
        action_set = set(actions)
        # Pending create (never existed in state) — not drift.
        if action_set == {"create"} and address not in prior_addresses:
            continue
        # Planned destroy because the resource was removed from code — not
        # external drift. External deletion shows up as create-from-state.
        if action_set == {"delete"}:
            continue
        if action_set == {"create"} and address in prior_addresses:
            deleted_addresses.add(address)
        entries.append(rc)
    return entries, deleted_addresses


def _lookup_file_path(file_index: dict, address: str | None) -> str | None:
    """Resolve a Terraform address to a .tf file path.

    Handles module prefixes (``module.vpc.aws_instance.foo``) and count /
    for_each indexes (``aws_instance.foo[0]``).
    """
    if not address or not file_index:
        return None
    bare = re.sub(r"\[.*?\]$", "", address)
    if bare in file_index:
        return file_index[bare]
    parts = bare.split(".")
    if len(parts) >= 2:
        candidate = f"{parts[-2]}.{parts[-1]}"
        if candidate in file_index:
            return file_index[candidate]
    return None


def report_drift(plan, tf_dir: str = None, scope: str | None = None) -> dict:
    prior_addresses = get_prior_state_addresses(plan)
    native_drift = list(plan.get("resource_drift") or [])
    change_entries, deleted_addresses = _entries_from_resource_changes(
        plan, prior_addresses
    )

    # Union both sources. When the same address appears in both, prefer
    # resource_changes — Terraform often puts only computed noise (tags_all)
    # in resource_drift while the actionable update is under resource_changes.
    # Keep native-only addresses (refresh drift with no matching plan change).
    # Track addresses taken from resource_changes so we can normalize their
    # before/after (live→config) into drift convention (config→live).
    by_address: dict = {}
    from_resource_changes: set[str] = set()
    for entry in native_drift:
        addr = entry.get("address")
        if addr:
            by_address[addr] = entry
    for entry in change_entries:
        addr = entry.get("address")
        if addr:
            by_address[addr] = entry
            from_resource_changes.add(addr)
    drift_entries = list(by_address.values())

    file_index = build_resource_file_index(tf_dir) if tf_dir else {}

    if not drift_entries:
        return {"report_type": "no_drift", "resources": []}

    resources = []
    for entry in drift_entries:
        if entry.get("mode") == "data":
            continue
        address = entry.get("address")
        change = entry.get("change", {})
        actions = change.get("actions", [])
        after = change.get("after") or {}
        action_set = set(actions)
        is_replace = action_set == {"create", "delete"}
        fpath = _lookup_file_path(file_index, address)

        # External deletion: create-from-state, or refresh showing all-null after.
        # Do NOT treat replace (delete+create) as deleted_externally.
        is_deleted = (
            not is_replace
            and (
                address in deleted_addresses
                or (
                    isinstance(change.get("before"), dict)
                    and isinstance(after, dict)
                    and change.get("before")
                    and after
                    and all(v is None for v in after.values())
                )
            )
        )

        if is_deleted:
            type_token = _resource_type_from_address(address or "")
            resources.append({
                "address": address,
                "status": "deleted_externally",
                "changes": {},
                "sensitive": False,
                "security_impact": (
                    "high"
                    if type_token in SECURITY_RESOURCE_TYPES
                    or type_token in HIGH_IMPACT_DELETION_TYPES
                    else "medium"
                ),
                "file_path": fpath,
            })
            continue

        before = change.get("before") if isinstance(change.get("before"), dict) else {}
        after_obj = after if isinstance(after, dict) else {}
        # resource_changes: before=live, after=config — invert to drift convention
        # (before=IaC/config, after=live) used by findings, PRs, and rollback.
        # resource_drift already uses prior-state→live (= drift convention).
        if address in from_resource_changes:
            before, after_obj = after_obj, before
        diffs = flatten_diff(before, after_obj)
        changes_dict = {field: {"before": b, "after": a} for field, b, a in diffs}
        changes_dict = {
            field: vals for field, vals in changes_dict.items()
            if field not in _READ_ONLY_DRIFT_ATTRS
        }
        # Update/replace with no visible attribute delta (sensitive-only,
        # after_unknown, provider quirks) is still real drift — don't drop it.
        if not changes_dict and (is_replace or "update" in action_set):
            changes_dict = {
                "_plan_change": {
                    "before": sorted(action_set),
                    "after": "sensitive_or_unknown_attribute_change",
                }
            }
        if not changes_dict:
            continue

        # If every drifted field is covered by lifecycle.ignore_changes,
        # this resource's rules are managed outside Terraform — don't
        # flag it as actionable drift.  Mark it externally_managed so
        # the reconciler routes it to needs-review instead of the LLM.
        if fpath:
            # ignore_changes is keyed by local resource name in the .tf file
            local_addr = ".".join(address.split(".")[-2:]).split("[", 1)[0] if address else address
            ignored = _get_ignored_fields(fpath, local_addr)
            if ignored and set(changes_dict.keys()).issubset(ignored):
                resources.append({
                    "address": address,
                    "status": "externally_managed",
                    "changes": {},
                    "sensitive": False,
                    "security_impact": classify_security_impact(address, changes_dict),
                    "file_path": fpath,
                    "_ignored_fields": sorted(ignored),
                })
                continue

        # ── Auto-suppress check ──
        resource_type = _resource_type_from_address(address or "")
        auto_fields = set()
        auto_reasons: list[str] = []
        for field in list(changes_dict.keys()):
            rule = _is_auto_suppressed(resource_type, field)
            if rule:
                auto_fields.add(field)
                auto_reasons.append(f"{field}: {rule['reason']}")

        if auto_fields == set(changes_dict.keys()):
            # Every drifted field is auto-suppressed — silence it completely.
            resources.append({
                "address": address,
                "status": "auto_suppressed",
                "changes": {},
                "sensitive": False,
                "security_impact": classify_security_impact(address, changes_dict),
                "file_path": fpath,
                "_auto_reasons": auto_reasons,
            })
        else:
            resources.append({
                "address": address,
                "changes": changes_dict,
                "sensitive": False,
                "security_impact": classify_security_impact(address, changes_dict),
                "file_path": fpath,
            })

    # Apply drift-exceptions registry if a terraform directory was provided.
    suppressed, expired_exc = [], []
    if tf_dir:
        active_exc, expired_exc = load_drift_exceptions(scope)
        if active_exc:
            suppressed, resources = apply_drift_exceptions(resources, active_exc)

    # Only count resources with actual changes or deleted_externally as
    # "drift".  Externally_managed resources (lifecycle.ignore_changes)
    # are informational — they don't need LLM analysis or a PR.
    actionable = [r for r in resources
                  if r.get("changes") or r.get("status") == "deleted_externally"]
    report = {"report_type": "drift" if actionable else "no_drift", "resources": resources}
    if suppressed:
        report["suppressed_resources"] = suppressed
    if expired_exc:
        report["expired_exceptions"] = expired_exc
    return report


_IGNORE_CHANGES_RE = re.compile(
    r'lifecycle\s*\{[^}]*ignore_changes\s*=\s*\[([^\]]*)\]',
    re.DOTALL,
)


def _get_ignored_fields(file_path: str, resource_address: str) -> set[str]:
    """Read the .tf file and return the set of field names covered by
    ``lifecycle.ignore_changes`` for the named resource.  Returns an
    empty set when the file can't be read, the resource isn't found, or
    no lifecycle block exists."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return set()

    if "." not in resource_address:
        return set()
    want_type, want_name = resource_address.split(".", 1)
    pattern = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')

    # ponytail: brace-count to find the resource block, then regex for
    # lifecycle.ignore_changes.  Fails inside heredocs / jsonencode.
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = pattern.search(line)
        if not m or m.group(1) != want_type or m.group(2) != want_name:
            continue
        depth = 0
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth == 0 and j > i:
                block = "\n".join(lines[i : j + 1])
                lc = _IGNORE_CHANGES_RE.search(block)
                if lc:
                    raw = lc.group(1)
                    fields = {f.strip().strip('"').strip("'") for f in raw.split(",")}
                    return {f for f in fields if f}
                return set()
    return set()


# ---------------------------------------------------------------------------
# Auto-suppress rules — these patterns match drift that is expected and
# should never trigger alerts or PRs (ASG-managed changes, AWS-managed
# tags, etc.).  Unlike drift-exceptions.json which requires human
# acknowledgement, auto-suppressed resources are filtered silently.
# ---------------------------------------------------------------------------

AUTO_SUPPRESS_RULES = [
    # ASG-managed tags on instances — AWS adds these, Terraform can't set them.
    {
        "resource_type": "aws_instance",
        "field_pattern": re.compile(r"^aws:"),
        "reason": "AWS-managed tag — cannot be set in Terraform",
    },
    # ASG scaling attributes — these change constantly by design.
    {
        "resource_type": "aws_autoscaling_group",
        "field_pattern": re.compile(r"^(desired_capacity|max_size|min_size|max_instance_lifetime)$"),
        "reason": "ASG scaling attribute — expected to change outside Terraform",
    },
]


def _is_auto_suppressed(resource_type: str, field_name: str) -> dict | None:
    """Return the matching rule dict if *field_name* on *resource_type*
    should be auto-suppressed, or ``None``."""
    for rule in AUTO_SUPPRESS_RULES:
        if rule["resource_type"] != resource_type:
            continue
        if rule["field_pattern"].search(field_name):
            return rule
    return None


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
        print("Usage: python formatting_drift_json.py <plan.json> [--account scope-a]")
        sys.exit(1)

    plan_file = sys.argv[1]
    scope = None
    if len(sys.argv) >= 4 and sys.argv[2] == "--account":
        scope = sys.argv[3]

    plan_data = load_plan(plan_file)
    result = report_drift(plan_data, tf_dir=os.path.dirname(plan_file), scope=scope)
    print(json.dumps(result))