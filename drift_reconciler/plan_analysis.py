"""Parse terraform plan JSON for destroy/replace risk."""
from __future__ import annotations

from typing import Any


def plan_destructive_changes(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return resources whose plan actions include delete (destroy or replace).

    Matches ``terraform plan`` output where replacement is
    ``# aws_instance.x must be replaced`` (actions contain ``delete``).
    """
    if not plan:
        return []
    out: list[dict[str, Any]] = []
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        if not actions or actions == ["no-op"]:
            continue
        if {"delete"} & set(actions):
            out.append({
                "address": rc.get("address"),
                "actions": actions,
                "replace_paths": change.get("replace_paths") or [],
            })
    return out


def log_plan_risks(plan: dict[str, Any] | None, *, prefix: str = "[apply]") -> list[dict[str, Any]]:
    """Log destructive plan entries and return them for callers."""
    risks = plan_destructive_changes(plan)
    for item in risks:
        print(
            f"{prefix} plan risk: address={item.get('address')} "
            f"actions={item.get('actions')} replace_paths={item.get('replace_paths')}"
        )
    return risks


def plan_risk_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Compact summary for API / pending_applies result."""
    risks = plan_destructive_changes(plan)
    return {
        "has_destroy_or_replace": bool(risks),
        "resources": risks,
    }
