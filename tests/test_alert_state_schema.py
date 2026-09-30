"""Regression: LangGraph must preserve alerts_sent / pr_urls on State."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from drift_findings import State


class AlertStateSchemaTests(unittest.TestCase):
    def test_alerts_sent_survives_graph_update(self):
        """Keys omitted from State are dropped by LangGraph 1.x — guard that."""

        def alert_node(state: State):
            return {"alerts_sent": {"pagerduty": 2, "slack": 1}}

        g = StateGraph(State)
        g.add_node("alert_agent", alert_node)
        g.add_edge(START, "alert_agent")
        g.add_edge("alert_agent", END)
        app = g.compile()

        out = app.invoke({})
        self.assertEqual(out.get("alerts_sent"), {"pagerduty": 2, "slack": 1})

        streamed = None
        for ev in app.stream({}):
            if "alert_agent" in ev:
                streamed = ev["alert_agent"]
        self.assertIsNotNone(streamed)
        self.assertEqual(streamed.get("alerts_sent"), {"pagerduty": 2, "slack": 1})

    def test_pr_urls_survives_graph_update(self):
        def pr_node(state: State):
            return {"pr_urls": [{"url": "https://example.com/pr/1", "type": "drift"}]}

        g = StateGraph(State)
        g.add_node("drift_pr", pr_node)
        g.add_edge(START, "drift_pr")
        g.add_edge("drift_pr", END)
        app = g.compile()

        out = app.invoke({})
        self.assertEqual(len(out.get("pr_urls") or []), 1)

    @patch("graph_nodes.pga.trigger_pagerduty_alert", return_value={"status": "success"})
    @patch("graph_nodes.slack.notify_all", return_value=1)
    @patch("graph_nodes._load_routing_rules", return_value={
        "HIGH": "pagerduty", "MEDIUM": "pagerduty", "LOW": "pagerduty",
    })
    def test_drift_alert_dispatches_and_returns_counts(self, _rules, mock_slack, mock_pd):
        import agent as ag
        from graph_nodes import drift_alert

        prev = ag._account_label
        ag._account_label = "scope-test"
        try:
            state = {
                "drift_detected": True,
                "drift_findings": [
                    {"resource_id": "aws_s3_bucket.b", "risk_level": "LOW", "status": "updated"},
                    {"resource_id": "aws_iam_role.r", "risk_level": "HIGH", "status": "updated"},
                ],
                "run_id": None,
            }
            out = drift_alert(state)
            self.assertEqual(out["alerts_sent"]["pagerduty"], 2)
            self.assertEqual(out["alerts_sent"]["slack"], 1)
            self.assertEqual(mock_pd.call_count, 2)
            # PagerDuty-routed findings also fan out to Slack.
            mock_slack.assert_called_once()
            self.assertEqual(len(mock_slack.call_args[0][0]), 2)
        finally:
            ag._account_label = prev

    @patch("graph_nodes.pga.trigger_pagerduty_alert", return_value={"status": "success"})
    @patch("graph_nodes.slack.notify_all", return_value=1)
    @patch("graph_nodes._load_routing_rules", return_value={
        "HIGH": "pagerduty", "MEDIUM": "slack", "LOW": "slack",
    })
    def test_risk_level_case_insensitive(self, _rules, mock_slack, mock_pd):
        import agent as ag
        from graph_nodes import drift_alert

        prev = ag._account_label
        ag._account_label = "scope-test"
        try:
            out = drift_alert({
                "drift_detected": True,
                "drift_findings": [
                    {"resource_id": "aws_s3_bucket.b", "risk_level": "high", "status": "updated"},
                ],
                "run_id": None,
            })
            self.assertEqual(out["alerts_sent"]["pagerduty"], 1)
            self.assertEqual(mock_pd.call_count, 1)
        finally:
            ag._account_label = prev


if __name__ == "__main__":
    unittest.main()
