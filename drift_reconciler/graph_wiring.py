"""Compile the LangGraph pipeline. Nodes must not import this module."""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from drift_findings import State, agent_node
from graph_nodes import (
    unmanaged_scan_node,
    trivy_gate,
    drift_alert,
    drift_pr_from_finding,
)

workflow = StateGraph(State)
workflow.add_node("unmanaged_scan", unmanaged_scan_node)
workflow.add_node("reconcile_agent", agent_node)
workflow.add_node("trivy_gate", trivy_gate)
workflow.add_node("alert_agent", drift_alert)
workflow.add_node("drift_pr", drift_pr_from_finding)

workflow.add_conditional_edges(
    START,
    lambda state: "unmanaged_scan" if state.get("scan_mode") in ("drift_and_unmanaged", "unmanaged_only") else "reconcile_agent",
    {"unmanaged_scan": "unmanaged_scan", "reconcile_agent": "reconcile_agent"},
)
workflow.add_conditional_edges(
    "unmanaged_scan",
    lambda state: "drift_pr" if state.get("scan_mode") == "unmanaged_only" else "reconcile_agent",
    {"drift_pr": "drift_pr", "reconcile_agent": "reconcile_agent"},
)
workflow.add_edge("reconcile_agent", "trivy_gate")
workflow.add_edge("trivy_gate", "alert_agent")
workflow.add_edge("trivy_gate", "drift_pr")
workflow.add_edge("alert_agent", END)
workflow.add_edge("drift_pr", END)

graph = workflow.compile()
