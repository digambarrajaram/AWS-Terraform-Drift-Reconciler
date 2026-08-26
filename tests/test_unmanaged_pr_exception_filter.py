"""Check the per-finding exception filter in drift_pr_from_finding:
a resource excepted by a previous merged PR (auto_add_exceptions_on_merge
wrote resource_type + resource_id_pattern) must NOT generate a new
unmanaged PR on the next scan — skipped as already excepted.

Run: python -m unittest tests.test_unmanaged_pr_exception_filter
"""
import os
import sys
import unittest

# agent.py uses plain top-level sibling imports (import unmanaged_scanner,
# import github_integration as gi, ...) — resolve them the same way the
# CLI does (python drift_reconciler/agent.py puts that dir first).
_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402


class FakePR:
    def __init__(self, number: int = 99):
        self.number = number
        self.html_url = f"https://github.com/x/y/pull/{number}"


class ExceptionFilterTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "account": agent._account_label,
            "pr": agent.gi.create_drift_pr_for_mode,
            "exc": agent.unmanaged_scanner._load_exceptions,
            "open": agent.drift_history.get_open_event,
            "stage": agent.report_stage,
            "pa": pending_applies.create_pending_apply,
        }
        agent._account_label = "prod-esign"
        agent.report_stage = lambda *a, **k: None
        agent.drift_history.get_open_event = lambda *a, **k: None
        pending_applies.create_pending_apply = lambda *a, **k: True
        self.created = []
        agent.gi.create_drift_pr_for_mode = (
            lambda finding, mode, account_label=None: self.created.append(finding) or FakePR()
        )

    def tearDown(self):
        agent._account_label = self._orig["account"]
        agent.gi.create_drift_pr_for_mode = self._orig["pr"]
        agent.unmanaged_scanner._load_exceptions = self._orig["exc"]
        agent.drift_history.get_open_event = self._orig["open"]
        agent.report_stage = self._orig["stage"]
        pending_applies.create_pending_apply = self._orig["pa"]

    def _finding(self, resource_id="aws_instance.prod-web"):
        return {
            "resource_id": resource_id,
            "risk_level": "MEDIUM",
            "drift_summary": "untracked",
            "file_path": None,
            "status": "unmanaged",
        }

    def test_merged_then_excepted_resource_never_gets_new_pr(self):
        # Previous run: PR #40 merged → auto_add_exceptions_on_merge wrote
        # an unmanaged row (aws_instance / prod-web).  The next scan finds
        # the same resource again → the filter must skip PR creation.
        agent.unmanaged_scanner._load_exceptions = lambda scope: [
            {"resource_type": "aws_instance", "resource_id_pattern": "prod-web"},
        ]
        state = {"drift_detected": True, "drift_findings": [self._finding()]}
        result = agent.drift_pr_from_finding(state)
        self.assertEqual(self.created, [])
        self.assertEqual(result["pr_urls"], [])

    def test_unexcepted_finding_still_creates_pr(self):
        agent.unmanaged_scanner._load_exceptions = lambda scope: []
        state = {"drift_detected": True, "drift_findings": [self._finding()]}
        result = agent.drift_pr_from_finding(state)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]["resource_id"], "aws_instance.prod-web")
        self.assertEqual(len(result["pr_urls"]), 1)

    def test_prefix_pattern_matches_like_scanner(self):
        # Manual dashboard rows may use a prefix pattern ("prod") —
        # substring semantics match the scanner's own suppression.
        agent.unmanaged_scanner._load_exceptions = lambda scope: [
            {"resource_type": "aws_instance", "resource_id_pattern": "prod"},
        ]
        state = {"drift_detected": True, "drift_findings": [self._finding()]}
        agent.drift_pr_from_finding(state)
        self.assertEqual(self.created, [])

    def test_other_type_rows_do_not_suppress(self):
        agent.unmanaged_scanner._load_exceptions = lambda scope: [
            {"resource_type": "aws_s3_bucket", "resource_id_pattern": "prod-web"},
        ]
        state = {"drift_detected": True, "drift_findings": [self._finding()]}
        agent.drift_pr_from_finding(state)
        self.assertEqual(len(self.created), 1)


if __name__ == "__main__":
    unittest.main()
