"""trivy-only: verify LLM fixes with a re-scan, and open one PR for all files.

Run: python -m unittest tests.test_trivy_verify_single_pr
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402
from drift_reconciler import trivy_only as to  # noqa: E402


class _FakePR:
    def __init__(self, number):
        self.number = number
        self.html_url = f"https://fake.example/pr/{number}"


class TrivyVerifySinglePrTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        self.tf_dir = tempfile.mkdtemp(prefix="trivy_verify_")
        with open(os.path.join(self.tf_dir, "main.tf"), "w") as f:
            f.write('resource "aws_sns_topic" "events" {\n  name = "e"\n}\n')
        with open(os.path.join(self.tf_dir, "lambda.tf"), "w") as f:
            f.write('resource "aws_lambda_function" "this" {\n  function_name = "f"\n}\n')
        self.pr_calls = []
        self._orig = {
            "trivy": agent._run_trivy,
            "fix": agent.fix_issues,
            "pr": agent.gi.create_drift_pr,
            "open": agent.drift_history.get_open_event,
            "stage": agent.report_stage,
            "pa": pending_applies.create_pending_apply,
            "sf": pending_applies.set_security_fixes,
            "rel": agent.gi.to_repo_relative_path,
            "verify": to._verify_fixes_cleared,
        }
        agent.gi.create_drift_pr = lambda **kw: self.pr_calls.append(kw) or _FakePR(1)
        agent.gi.to_repo_relative_path = lambda p: os.path.basename(p)
        agent.drift_history.get_open_event = lambda *a, **k: None
        agent.report_stage = lambda *a, **k: None
        pending_applies.create_pending_apply = lambda *a, **k: True
        pending_applies.set_security_fixes = lambda *a, **k: True

    def tearDown(self):
        agent._run_trivy = self._orig["trivy"]
        agent.fix_issues = self._orig["fix"]
        agent.gi.create_drift_pr = self._orig["pr"]
        agent.gi.to_repo_relative_path = self._orig["rel"]
        agent.drift_history.get_open_event = self._orig["open"]
        agent.report_stage = self._orig["stage"]
        pending_applies.create_pending_apply = self._orig["pa"]
        pending_applies.set_security_fixes = self._orig["sf"]
        to._verify_fixes_cleared = self._orig["verify"]
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_ineffective_fix_goes_to_manual_review_not_fix_pr(self):
        """LLM rewrite that still fails Trivy must not open a fix PR."""
        agent._run_trivy = lambda d: {"Results": [{"Target": "main.tf", "Misconfigurations": [{
            "AVDID": "AWS-0095", "Severity": "HIGH", "Title": "Unencrypted SNS",
            "Description": "x", "Resolution": "Enable encryption", "Status": "FAIL",
            "CauseMetadata": {"Resource": "aws_sns_topic.events",
                              "StartLine": 1, "EndLine": 3},
        }]}]}
        agent.fix_issues = lambda state: {
            "fixes_applied": [{
                "file_path": os.path.join(state["tf_dir"], "main.tf"),
                "rule_id": "AWS-0095",
                "description": "LLM rewrote block",
                "resource": "aws_sns_topic.events",
            }],
            "needs_review": [],
        }
        # Keep real verify — re-scan still returns AWS-0095.
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-setup", "prod-setup")
        fix_prs = [k for k in self.pr_calls if not k.get("review_only")]
        self.assertEqual(fix_prs, [])
        self.assertTrue(any(k.get("review_only") for k in self.pr_calls))
        self.assertTrue(any(
            n.get("reason") == "automated fix did not clear Trivy finding"
            for n in result["needs_review"]
        ))

    def test_verified_fixes_across_two_files_open_one_pr(self):
        calls = {"n": 0}

        def fake_trivy(d):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"Results": [
                    {"Target": "main.tf", "Misconfigurations": [{
                        "AVDID": "AWS-0095", "Severity": "HIGH", "Title": "SNS",
                        "Description": "x", "Resolution": "encrypt", "Status": "FAIL",
                        "CauseMetadata": {"Resource": "aws_sns_topic.events",
                                          "StartLine": 1, "EndLine": 3},
                    }]},
                    {"Target": "lambda.tf", "Misconfigurations": [{
                        "AVDID": "AWS-0066", "Severity": "MEDIUM", "Title": "XRay",
                        "Description": "x", "Resolution": "enable", "Status": "FAIL",
                        "CauseMetadata": {"Resource": "aws_lambda_function.this",
                                          "StartLine": 1, "EndLine": 3},
                    }]},
                ]}
            return {"Results": []}

        def fake_fix(state):
            p1 = os.path.join(state["tf_dir"], "main.tf")
            p2 = os.path.join(state["tf_dir"], "lambda.tf")
            with open(p1, "a", encoding="utf-8") as f:
                f.write("\n# sns fix\n")
            with open(p2, "a", encoding="utf-8") as f:
                f.write("\n# lambda fix\n")
            return {
                "fixes_applied": [
                    {"file_path": p1, "rule_id": "AWS-0095",
                     "description": "sns", "resource": "aws_sns_topic.events"},
                    {"file_path": p2, "rule_id": "AWS-0066",
                     "description": "xray", "resource": "aws_lambda_function.this"},
                ],
                "needs_review": [],
            }

        agent._run_trivy = fake_trivy
        agent.fix_issues = fake_fix
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-setup", "prod-setup")
        fix_prs = [k for k in self.pr_calls if not k.get("review_only")]
        self.assertEqual(len(fix_prs), 1)
        self.assertEqual(fix_prs[0]["resource_id"], "trivy-security")
        self.assertTrue(fix_prs[0].get("additional_files"))
        self.assertEqual(len(fix_prs[0]["additional_files"]), 1)
        self.assertEqual(result["pr_urls"][0]["type"], "security_only")


if __name__ == "__main__":
    unittest.main()
