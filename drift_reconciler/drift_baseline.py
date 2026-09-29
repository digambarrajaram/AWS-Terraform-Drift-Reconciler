"""Rollback baselines for drift events (including deleted_externally)."""
from __future__ import annotations

import json

# Synthetic plan field — never written to HCL (filtered from patches).
DELETED_EXTERNALLY_FIELD = "__deleted_externally__"

# Plan JSON represents “no tags” as null or {}; treat as equivalent for drift/gates.
_EMPTY_MAP_FIELDS = frozenset({"tags", "tags_all"})


def normalize_plan_field_for_compare(field: str, val):
    if field in _EMPTY_MAP_FIELDS and (val is None or val == {}):
        return {}
    return val


def plan_field_values_equal(field: str, a, b) -> bool:
    return normalize_plan_field_for_compare(field, a) == normalize_plan_field_for_compare(field, b)


def plan_field_baseline_token(field: str, val) -> str:
    """Stable string for comparing stored baselines to live plan ``before`` values."""
    norm = normalize_plan_field_for_compare(field, val)
    if norm is None:
        return ""
    return str(norm)


_DELETED_SUMMARY_MARKERS = (
    "deleted outside of terraform",
    "missing from aws",
)


def deleted_externally_baseline() -> dict:
    """Baseline shape stored in ``changes_jsonb`` for external deletions."""
    return {
        DELETED_EXTERNALLY_FIELD: {
            "before": "present_in_aws",
            "after": "missing_in_aws",
        }
    }


def is_deleted_externally_baseline(changes: dict | None) -> bool:
    return bool(changes and DELETED_EXTERNALLY_FIELD in changes)


def changes_for_history(finding: dict) -> dict | None:
    """``changes_jsonb`` payload for Supabase from an in-memory finding."""
    if finding.get("status") == "deleted_externally":
        return deleted_externally_baseline()
    changes = finding.get("changes") or {}
    return changes or None


def infer_baseline_from_row(row: dict) -> dict | None:
    """Reconstruct baselines for older rows written before sentinel support."""
    raw = row.get("changes_jsonb")
    if raw:
        changes = (
            json.loads(raw) if isinstance(raw, str) else raw
        )
        if changes:
            return changes
    summary = (row.get("drift_summary") or "").lower()
    if any(marker in summary for marker in _DELETED_SUMMARY_MARKERS):
        return deleted_externally_baseline()
    return None


def resource_plan_actions(plan_json: dict, resource_address: str) -> set[str] | None:
    for rc in plan_json.get("resource_changes", []):
        if rc.get("address") == resource_address:
            return set(rc.get("change", {}).get("actions", []))
    return None


def verify_deleted_externally_plan(
    plan_json: dict,
    resource_id: str,
    *,
    is_revert: bool,
) -> str | None:
    """Return a gate failure message, or None when the plan matches expectations."""
    actions = resource_plan_actions(plan_json, resource_id)
    if actions is None:
        return (
            f"rollback_check: baseline for {resource_id} not "
            f"found in current plan — cannot verify revert safety"
        )

    non_noop = actions - {"no-op"}
    if is_revert:
        if not non_noop:
            return (
                f"rollback_check: stale plan for {resource_id} — "
                f"expected create to restore deleted resource, got no-op"
            )
        if "create" not in actions:
            return (
                f"rollback_check: stale plan for {resource_id} — "
                f"expected create to restore deleted resource, got {sorted(actions)}"
            )
        return None

    # Applying a code_to_reality fix: drop missing resource from state / code.
    if "delete" in actions or not non_noop:
        return None
    return (
        f"rollback_check: unexpected plan for deleted_externally fix on "
        f"{resource_id} — got {sorted(actions)}"
    )
