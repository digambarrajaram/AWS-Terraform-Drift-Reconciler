"""Security PRs store whole-file __tf_file__ baselines — preview must not
look up resource_id in terraform plan (trivy-security is not an address).

Run: python -m unittest tests.test_rollback_preview_tf_file
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
from drift_reconciler.drift_baseline import tf_file_baseline  # noqa: E402


class RollbackPreviewTfFileTests(unittest.TestCase):
    def setUp(self):
        self.tf_dir = tempfile.mkdtemp(prefix="rollback_prev_tf_")
        self.tf_file = os.path.join(self.tf_dir, "trivy-e2e.tf")
        self.fixed = (
            'resource "aws_ecr_repository" "trivy_e2e_mutable_tags" {\n'
            '  image_tag_mutability = "IMMUTABLE"\n'
            "}\n"
        )
        self.original = (
            'resource "aws_ecr_repository" "trivy_e2e_mutable_tags" {\n'
            '  image_tag_mutability = "MUTABLE"\n'
            "}\n"
        )
        with open(self.tf_file, "w", encoding="utf-8") as fh:
            fh.write(self.fixed)

        self.updated_runs = []
        self._orig = {
            "baselines": rollback_flow._load_rollback_baselines,
            "fetch": rollback_flow._fetch_live_state,
            "stage": rollback_flow._report_rollback_stage,
            "workspace": rollback_flow._prepare_terraform_workspace,
        }
        rollback_flow._load_rollback_baselines = lambda pr, scope: [{
            "resource_id": "trivy-security",
            "file_path": "trivy-e2e.tf",
            "changes": tf_file_baseline(self.original, self.fixed),
        }]
        rollback_flow._fetch_live_state = lambda *a, **k: (
            self.fail("_fetch_live_state must not run for __tf_file__ baselines"),
            {},
        )
        rollback_flow._report_rollback_stage = lambda *a, **k: None
        rollback_flow._prepare_terraform_workspace = lambda tf_dir, scope: ({}, {})

    def tearDown(self):
        rollback_flow._load_rollback_baselines = self._orig["baselines"]
        rollback_flow._fetch_live_state = self._orig["fetch"]
        rollback_flow._report_rollback_stage = self._orig["stage"]
        rollback_flow._prepare_terraform_workspace = self._orig["workspace"]

    def test_preview_populates_diff_from_file_not_plan(self):
        with mock.patch.object(
            gi, "resolve_repo_relative_path", return_value=self.tf_file,
        ):
            with mock.patch(
                "rollback_runs.update_rollback_run",
                side_effect=lambda run_id, **kw: self.updated_runs.append(kw),
            ):
                rollback_flow._run_rollback_preview(
                    self.tf_dir, pr_number=313, scope="prod-setup", run_id="run-1",
                )

        self.assertEqual(len(self.updated_runs), 1)
        self.assertEqual(self.updated_runs[0]["status"], "complete")
        diff = self.updated_runs[0]["result"]["diff"]
        self.assertEqual(len(diff), 1)
        row = diff[0]
        self.assertEqual(row["resource_id"], "trivy-security")
        self.assertEqual(row["field"], "__tf_file__")
        self.assertEqual(row["fixed"], self.fixed)
        self.assertEqual(row["original"], self.original)
        self.assertEqual(row["current_live"], self.fixed)


if __name__ == "__main__":
    unittest.main()
