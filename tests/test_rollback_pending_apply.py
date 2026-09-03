"""Rollback execute must enqueue the new PR on Approvals via create_pending_apply.

Gap covered: execute used to open a GitHub PR and stop — fix/batch/security
already insert pending_applies at PR-create time; rollback must do the same.

Run: python -m unittest tests.test_rollback_pending_apply
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import rollback_flow  # noqa: E402
import github_integration as gi  # noqa: E402


class _FakePR:
    def __init__(self, number):
        self.number = number
        self.html_url = f"https://fake.example/pr/{number}"


class RollbackPendingApplyTests(unittest.TestCase):
    def setUp(self):
        self.tf_dir = tempfile.mkdtemp(prefix="rollback_pa_")
        self.tf_file = os.path.join(self.tf_dir, "main.tf")
        with open(self.tf_file, "w", encoding="utf-8") as fh:
            fh.write('resource "aws_s3_bucket" "b" {\n  bucket = "orig"\n}\n')

        self.pending = []
        self.pr_calls = []
        self._orig = {
            "baselines": rollback_flow._load_rollback_baselines,
            "fetch": rollback_flow._fetch_live_state,
            "stage": rollback_flow._report_rollback_stage,
            "sub_env": rollback_flow._terraform_sub_env_for_scope,
            "apply": gi.apply_changes_to_file,
            "pr": gi.create_drift_pr,
            "rel": gi.to_repo_relative_path,
        }

        rollback_flow._load_rollback_baselines = lambda pr, scope: [{
            "resource_id": "aws_s3_bucket.b",
            "file_path": "main.tf",
            "changes": {"bucket": {"before": "orig", "after": "fixed"}},
        }]
        rollback_flow._fetch_live_state = lambda *a, **k: (
            "present", {"bucket": "fixed"},
        )
        rollback_flow._report_rollback_stage = lambda *a, **k: None
        rollback_flow._terraform_sub_env_for_scope = lambda scope: {}
        gi.apply_changes_to_file = lambda *a, **k: (
            'resource "aws_s3_bucket" "b" {\n  bucket = "orig"\n}\n'
        )
        gi.create_drift_pr = lambda **kw: self.pr_calls.append(kw) or _FakePR(42)
        gi.to_repo_relative_path = lambda p: os.path.basename(p)

        # agent._account_label is read inside _do_run_rollback
        import agent as _ag
        self._ag = _ag
        self._orig_label = getattr(_ag, "_account_label", None)
        _ag._account_label = "scope-a"

    def tearDown(self):
        rollback_flow._load_rollback_baselines = self._orig["baselines"]
        rollback_flow._fetch_live_state = self._orig["fetch"]
        rollback_flow._report_rollback_stage = self._orig["stage"]
        rollback_flow._terraform_sub_env_for_scope = self._orig["sub_env"]
        gi.apply_changes_to_file = self._orig["apply"]
        gi.create_drift_pr = self._orig["pr"]
        gi.to_repo_relative_path = self._orig["rel"]
        self._ag._account_label = self._orig_label

    def test_execute_enqueues_pending_apply_as_rollback(self):
        with mock.patch(
            "drift_reconciler.pending_applies.create_pending_apply",
            side_effect=lambda *a, **k: self.pending.append((a, k)) or True,
        ):
            # resolve_repo_relative_path must map baseline file_path → abs path
            with mock.patch.object(
                gi, "resolve_repo_relative_path", return_value=self.tf_file,
            ):
                rollback_flow._do_run_rollback(self.tf_dir, pr_number=7, run_id=None)

        self.assertEqual(len(self.pending), 1, self.pending)
        args, _kwargs = self.pending[0]
        self.assertEqual(args, (42, "scope-a", "rollback"))
        self.assertEqual(len(self.pr_calls), 1)
        self.assertEqual(
            self.pr_calls[0]["changes"],
            {"bucket": {"before": "fixed", "after": "orig"}},
        )
        self.assertTrue(self.pr_calls[0]["is_rollback"])
        self.assertEqual(self.pr_calls[0]["resource_id"], "aws_s3_bucket.b-rollback")


if __name__ == "__main__":
    unittest.main()
