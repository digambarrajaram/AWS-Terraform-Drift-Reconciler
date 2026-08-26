"""Check run_trivy_only_scan creates review-only PRs for unfixable trivy
findings: fix_issues returns no fixes but a needs_review item → a PR with
no file diff is created (review_only=True, security path), lands in the
approval queue via create_pending_apply, and its (resource, rule_id) pairs
are persisted for merge-time exception adds.  A second test covers the
dedup skip and a third that review PRs coexist with fix PRs.

Run: python -m unittest tests.test_manual_review_pr
"""
import os
import sys
import tempfile
import unittest

# agent.py uses plain top-level sibling imports — resolve them the same
# way the CLI does (python drift_reconciler/agent.py puts that dir first).
_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402


class _FakePR:
    def __init__(self, number):
        self.number = number
        self.html_url = f"https://fake.example/pr/{number}"


def _fake_trivy_output(tmpdir):
    """One failing misconfig on main.tf — the shape _extract_issues parses."""
    return {"Results": [{"Target": "main.tf", "Misconfigurations": [{
        "AVDID": "AVD-AWS-0107", "Severity": "HIGH", "Title": "SG wide open",
        "Description": "Security group allows 0.0.0.0/0",
        "Resolution": "Restrict the CIDR", "Status": "FAIL",
        "CauseMetadata": {"Resource": "aws_security_group.foo",
                          "StartLine": 1, "EndLine": 4},
    }]}]}


class ManualReviewPrTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        self.tf_dir = tempfile.mkdtemp(prefix="review_pr_")
        with open(os.path.join(self.tf_dir, "main.tf"), "w") as f:
            f.write('resource "aws_security_group" "foo" {\n  name = "foo"\n}\n')
        self.pr_calls = []
        self.pending = []
        self.fix_pairs = []
        self.open_rows = []

        self._orig = {
            "trivy": agent._run_trivy,
            "fix": agent.fix_issues,
            "pr": agent.gi.create_drift_pr,
            "open": agent.drift_history.get_open_event,
            "stage": agent.report_stage,
            "pa": pending_applies.create_pending_apply,
            "sf": pending_applies.set_security_fixes,
        }
        agent._run_trivy = lambda tmpdir: _fake_trivy_output(tmpdir)
        agent.fix_issues = lambda state: {
            "fixes_applied": [], "needs_review": [{
                "rule_id": "AVD-AWS-0107",
                "resource": "aws_security_group.foo",
                "resolution": "Restrict the CIDR",
                "reason": "no applicable automated fix",
            }],
        }
        agent.gi.create_drift_pr = lambda **kw: self.pr_calls.append(kw) or _FakePR(1)
        agent.drift_history.get_open_event = lambda *a, **k: \
            (self.open_rows.pop(0) if self.open_rows else None)
        agent.report_stage = lambda *a, **k: None
        pending_applies.create_pending_apply = lambda *a, **k: self.pending.append(a)
        pending_applies.set_security_fixes = lambda *a, **k: self.fix_pairs.append(a)

    def tearDown(self):
        agent._run_trivy = self._orig["trivy"]
        agent.fix_issues = self._orig["fix"]
        agent.gi.create_drift_pr = self._orig["pr"]
        agent.drift_history.get_open_event = self._orig["open"]
        agent.report_stage = self._orig["stage"]
        pending_applies.create_pending_apply = self._orig["pa"]
        pending_applies.set_security_fixes = self._orig["sf"]
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_unfixable_finding_gets_review_pr(self):
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-esign", "prod-esign")
        self.assertEqual(len(self.pr_calls), 1)
        kw = self.pr_calls[0]
        self.assertTrue(kw["review_only"])
        self.assertTrue(kw["security"])
        self.assertEqual(kw["resource_id"], "aws_security_group.foo")
        self.assertEqual(kw["file_content"], "")  # no file diff
        self.assertIn("AVD-AWS-0107", kw["drift_summary"])
        self.assertIn("no applicable automated fix", kw["drift_summary"])
        self.assertIn("Restrict the CIDR", kw["plan_output"])
        self.assertEqual(self.pending, [(1, "prod-esign", "security_only")])
        self.assertEqual(self.fix_pairs, [(1, "prod-esign",
            [{"resource_address": "aws_security_group.foo", "rule_id": "AVD-AWS-0107"}])])
        self.assertEqual(result["pr_urls"], [{"url": "https://fake.example/pr/1", "type": "manual"}])
        self.assertEqual(len(result["needs_review"]), 1)

    def test_open_review_pr_blocks_duplicate(self):
        self.open_rows.append({"pr_number": 9})
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-esign", "prod-esign")
        self.assertEqual(self.pr_calls, [])
        self.assertEqual(result["pr_urls"], [])
        self.assertEqual(self.pending, [])

    def test_review_pr_coexists_with_fix_pr(self):
        agent.fix_issues = lambda state: {
            "fixes_applied": [{"file_path": os.path.join(self.tf_dir, "main.tf"),
                               "rule_id": "AVD-AWS-0106", "description": "fixed"}],
            "needs_review": [{
                "rule_id": "AVD-AWS-0107",
                "resource": "aws_security_group.foo",
                "resolution": "Restrict the CIDR",
                "reason": "no applicable automated fix",
            }],
        }
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-esign", "prod-esign")
        self.assertEqual(len(self.pr_calls), 2)  # fix PR + review PR
        self.assertEqual(len([k for k in self.pr_calls if k.get("review_only")]), 1)
        types = {entry["type"] for entry in result["pr_urls"]}
        self.assertEqual(types, {"security_only", "manual"})


if __name__ == "__main__":
    unittest.main()
