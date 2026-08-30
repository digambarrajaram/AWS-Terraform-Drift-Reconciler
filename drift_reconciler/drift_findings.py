"""Drift finding builders and reconcile agent_node."""
from __future__ import annotations

import json
from typing import Annotated

from typing_extensions import TypedDict
from langchain_core.messages import AIMessage
from drift_reconciler.llm_client import _get_llm
from scan_runs import report_stage

class State(TypedDict):
    messages: Annotated[list, lambda x, y: x + y]
    drift_detected: bool
    drift_findings: list[dict]   # one entry per drifted resource
    trivy_scanned: bool
    scan_unmanaged: bool
    scan_mode: str
    run_id: str | None
    terraform_failed: bool


def map_risk(security_impact) -> str:
    return {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(security_impact, "LOW")


# Resource types and change patterns that alter network reachability.
# The LLM's freeform security_impact may misclassify these as "low"
# (e.g. a removed default route).  Any finding matching these rules
# gets upgraded to at least MEDIUM.
_NETWORK_RISK_TYPES = frozenset({
    "aws_route_table", "aws_route_table_association",
    "aws_internet_gateway", "aws_nat_gateway",
    "aws_vpc_peering_connection", "aws_vpc_peering_connection_accepter",
    "aws_security_group", "aws_security_group_rule",
    "aws_vpc_security_group_ingress_rule", "aws_vpc_security_group_egress_rule",
    "aws_network_acl", "aws_network_acl_rule",
})
_NETWORK_RISK_FIELDS = frozenset({"route", "routes", "cidr_block",
    "cidr_ipv4", "cidr_ipv6", "cidr_blocks", "ipv6_cidr_blocks",
    "ingress", "egress", "destination_cidr_block", "gateway_id",
    "nat_gateway_id", "internet_gateway_id", "vpc_peering_connection_id",
})


def _min_network_risk(resource_id: str, changes: dict) -> str | None:
    """Return ``"MEDIUM"`` if *resource_id* and *changes* match a
    network-reachability pattern, ``None`` otherwise (no upgrade)."""
    rtype = resource_id.split(".")[0] if "." in resource_id else resource_id
    if rtype not in _NETWORK_RISK_TYPES:
        return None
    if any(f in _NETWORK_RISK_FIELDS for f in changes):
        return "MEDIUM"
    return None


def build_drift_summary(resource: dict) -> str:
    if resource.get("status") == "deleted_externally":
        return "Resource was deleted outside of Terraform (found in state, missing from AWS)."
    if resource.get("status") == "externally_managed":
        ignored = resource.get("_ignored_fields", [])
        return f"Drift on fields covered by lifecycle.ignore_changes ({', '.join(ignored)}) — managed outside Terraform."
    changes = resource.get("changes", {})
    lines = [f"- `{field}`: before `{v.get('before')}` → after `{v.get('after')}`" for field, v in changes.items()]
    return "\n".join(lines)


def build_drift_findings(drift_report_json: dict) -> list[dict]:
    findings = []
    if drift_report_json.get("report_type") != "drift":
        return findings

    for resource in drift_report_json.get("resources", []):
        # deleted_externally / externally_managed resources have no
        # "changes" but are still real findings worth reporting.
        status = resource.get("status")
        if not resource.get("changes") and status not in ("deleted_externally", "externally_managed"):
            continue
        risk = map_risk(resource.get("security_impact"))
        # Network-reachability changes are never "low" — upgrade the LLM's
        # freeform classification when the resource type + changed fields
        # match a known network-risk pattern.
        net_upgrade = _min_network_risk(resource["address"], resource.get("changes", {}))
        if net_upgrade and risk == "LOW":
            risk = net_upgrade

        dr_summary = build_drift_summary(resource)
        if net_upgrade and map_risk(resource.get("security_impact")) == "LOW":
            dr_summary = ("> ⚠ **Network-reachability change** — severity "
                          "upgraded from Low to Medium.\n\n" + dr_summary)

        findings.append({
            "resource_id": resource["address"],
            "risk_level": risk,
            "drift_summary": dr_summary,
            "plan_output": json.dumps(resource.get("changes") or {"status": status}, indent=2),
            "file_path": resource.get("file_path"),
            "changes": resource.get("changes", {}),
            "status": status,
        })
    return findings



def agent_node(state: State):
    import agent as _ag
    report_stage = _ag.report_stage
    report_stage(state.get("run_id"), "reconcile_agent")
    raw_report_str = ""
    for msg in state["messages"]:
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if "processed drift report" in content:
            raw_report_str = content
            break

    try:
        json_start = raw_report_str.index("{")
        json_end = raw_report_str.rindex("}") + 1
        drift_report_json = json.loads(raw_report_str[json_start:json_end])
    except (ValueError, json.JSONDecodeError):
        drift_report_json = {"report_type": "unknown", "resources": []}

    drift_detected = drift_report_json.get("report_type") == "drift"

    if not drift_detected:
        # Preserve any unmanaged findings that were already attached
        # by the optional unmanaged-scan node.
        existing = state.get("drift_findings") or []
        return {
            "messages": [AIMessage(content="STATUS: NO_DRIFT\nNo configuration drift detected.")],
            "drift_detected": state.get("drift_detected", False),
            "drift_findings": existing,
        }

    # Strip externally_managed resources from the LLM prompt — the LLM
    # should only see actionable drift it can propose fixes for.
    actionable_resources = [
        r for r in drift_report_json.get("resources", [])
        if r.get("status") != "externally_managed"
    ]
    clean_report = dict(drift_report_json, resources=actionable_resources)
    llm_messages = []
    for msg in state["messages"]:
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if "processed drift report" in content:
            llm_messages.append({"role": msg.get("role", "user"),
                                 "content": content.replace(
                                     raw_report_str[json_start:json_end],
                                     json.dumps(clean_report))})
        else:
            llm_messages.append(msg)

    response = _get_llm().invoke(llm_messages)
    drift_only = build_drift_findings(drift_report_json)
    # Merge any unmanaged findings that were already attached by the
    # optional unmanaged-scan node so they survive the state update.
    existing = state.get("drift_findings") or []
    merged = existing + drift_only
    # Sort so findings with cost impact appear first (highest $ first)
    # — the LLM sees the most expensive untracked resources upfront.
    merged.sort(
        key=lambda f: (f.get("cost_impact") or {}).get("monthly_estimate_usd", -1),
        reverse=True,
    )
    return {
        "messages": [response],
        "drift_detected": True,
        "drift_findings": merged,
    }

