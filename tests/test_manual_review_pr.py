"""Check run_trivy_only_scan creates review-only PRs for unfixable trivy
findings: fix rejection (e.g. resource-count safety) → needs_review → a
PR with a drift-reports/*.md commit (GitHub requires a commit; empty
review branches used to 422 and mark the scan failed at trivy_only_review),
lands in Approvals via create_pending_apply, and finalizes scan_runs as
complete — never failed.

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
import scan_runs  # noqa: E402


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
        self.scan_writes = []

        self._orig = {
            "trivy": agent._run_trivy,
            "fix": agent.fix_issues,
            "pr": agent.gi.create_drift_pr,
            "open": agent.drift_history.get_open_event,
            "stage": agent.report_stage,
            "pa": pending_applies.create_pending_apply,
            "sf": pending_applies.set_security_fixes,
            "upd": scan_runs.update_scan_run,
            "rel": agent.gi.to_repo_relative_path,
        }
        agent._run_trivy = lambda tmpdir: _fake_trivy_output(tmpdir)
        # Simulate resource-count rejection fallthrough: no fixes, one
        # needs_review item (same shape fix_issues appends on REJECTED).
        agent.fix_issues = lambda state: {
            "fixes_applied": [], "needs_review": [{
                "rule_id": "AVD-AWS-0178",
                "resource": "aws_db_instance.foo",
                "resolution": "Enable IAM auth",
                "reason": "no applicable automated fix",
            }],
        }
        agent.gi.create_drift_pr = lambda **kw: self.pr_calls.append(kw) or _FakePR(1)
        agent.gi.to_repo_relative_path = lambda p: os.path.basename(p)
        agent.drift_history.get_open_event = lambda *a, **k: \
            (self.open_rows.pop(0) if self.open_rows else None)
        agent.report_stage = lambda *a, **k: None
        pending_applies.create_pending_apply = lambda *a, **k: self.pending.append((a, k))
        pending_applies.set_security_fixes = lambda *a, **k: self.fix_pairs.append(a)
        scan_runs.update_scan_run = lambda run_id, **fields: self.scan_writes.append(
            {"run_id": run_id, **fields}
        )

    def tearDown(self):
        agent._run_trivy = self._orig["trivy"]
        agent.fix_issues = self._orig["fix"]
        agent.gi.create_drift_pr = self._orig["pr"]
        agent.gi.to_repo_relative_path = self._orig["rel"]
        agent.drift_history.get_open_event = self._orig["open"]
        agent.report_stage = self._orig["stage"]
        pending_applies.create_pending_apply = self._orig["pa"]
        pending_applies.set_security_fixes = self._orig["sf"]
        scan_runs.update_scan_run = self._orig["upd"]
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_unfixable_finding_gets_review_pr(self):
        result = agent.run_trivy_only_scan(self.tf_dir, "prod-esign", "prod-esign")
        self.assertEqual(len(self.pr_calls), 1)
        kw = self.pr_calls[0]
        self.assertTrue(kw["review_only"])
        self.assertTrue(kw["security"])
        self.assertEqual(kw["resource_id"], "aws_db_instance.foo")
        # Must ship a markdown report commit — empty head==base PRs 422.
        self.assertTrue(kw["file_content"])
        self.assertIn("drift-reports/", kw["file_path"])
        self.assertIn("AVD-AWS-0178", kw["drift_summary"])
        self.assertIn("no applicable automated fix", kw["drift_summary"])
        self.assertIn("Enable IAM auth", kw["plan_output"])
        self.assertEqual(self.pending, [((1, "prod-esign", "security_only"), {"review_only": True})])
        self.assertEqual(self.fix_pairs, [(1, "prod-esign",
            [{"resource_address": "aws_db_instance.foo", "rule_id": "AVD-AWS-0178"}])])
        self.assertEqual(result["pr_urls"], [{"url": "https://fake.example/pr/1", "type": "manual"}])
        self.assertEqual(len(result["needs_review"]), 1)

    def test_fix_rejection_with_review_pr_marks_scan_complete_not_failed(self):
        """Resource-count (or any) fix rejection → review PR → status=complete."""
        results = agent.run_trivy_only_scan(
            self.tf_dir, "prod-esign", "prod-esign", run_id="run-1",
        )
        self.assertTrue(results["pr_urls"])
        agent.finalize_trivy_only_scan("run-1", results)
        self.assertEqual(len(self.scan_writes), 1)
        write = self.scan_writes[0]
        self.assertEqual(write["status"], "complete")
        self.assertNotEqual(write["status"], "failed")
        self.assertEqual(write["result_summary"]["mode"], "trivy_only")
        self.assertEqual(
            write["result_summary"]["security"]["pr_links"],
            ["https://fake.example/pr/1"],
        )

    def test_review_pr_github_failure_does_not_fail_scan(self):
        """One create_drift_pr crash must not abort the trivy_only run."""
        agent.gi.create_drift_pr = lambda **kw: (_ for _ in ()).throw(
            RuntimeError("No commits between base and head")
        )
        results = agent.run_trivy_only_scan(self.tf_dir, "prod-esign", "prod-esign")
        self.assertEqual(results["pr_urls"], [])
        self.assertEqual(len(results["needs_review"]), 1)
        agent.finalize_trivy_only_scan("run-2", results)
        self.assertEqual(self.scan_writes[0]["status"], "complete")

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
                "rule_id": "AVD-AWS-0178",
                "resource": "aws_db_instance.foo",
                "resolution": "Enable IAM auth",
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
