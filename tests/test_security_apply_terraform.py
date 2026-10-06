"""Real-fix security_only PRs must terraform apply (not file-only).

Review-only security stays file-only.  Whole-file ``__tf_file__`` baselines
pass Gate B so apply is not blocked for missing field-level drift tokens.

Run: python -m unittest tests.test_security_apply_terraform
"""
from __future__ import annotations

import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402
from drift_reconciler import drift_history as dh  # noqa: E402
from drift_reconciler.drift_baseline import (  # noqa: E402
    check_baseline_freshness,
    tf_file_baseline,
    is_tf_file_baseline,
)


class SecurityRequiresTerraformTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "gpt": dh.get_pr_type,
            "iro": pending_applies.is_review_only,
        }

    def tearDown(self):
        dh.get_pr_type = self._orig["gpt"]
        pending_applies.is_review_only = self._orig["iro"]

    def test_real_fix_security_requires_terraform(self):
        dh.get_pr_type = lambda *a, **k: "security_only"
        pending_applies.is_review_only = lambda *a, **k: False
        self.assertTrue(agent._pr_requires_terraform(42, "prod-setup"))

    def test_review_only_security_is_file_only(self):
        dh.get_pr_type = lambda *a, **k: "security_only"
        pending_applies.is_review_only = lambda *a, **k: True
        self.assertFalse(agent._pr_requires_terraform(43, "prod-setup"))

    def test_unmanaged_still_file_only(self):
        dh.get_pr_type = lambda *a, **k: "unmanaged"
        pending_applies.is_review_only = lambda *a, **k: False
        self.assertFalse(agent._pr_requires_terraform(44, "prod-setup"))


class TfFileBaselineGateTests(unittest.TestCase):
    def test_tf_file_baseline_passes_freshness_gate(self):
        baselines = [{
            "resource_id": "trivy-security",
            "changes": tf_file_baseline("old\n", "new\n"),
            "file_path": "main.tf",
        }]
        self.assertTrue(is_tf_file_baseline(baselines[0]["changes"]))
        err = check_baseline_freshness(
            {"resource_changes": []},
            baselines,
            rollback_semantics=False,
            extract_field_values=lambda *a, **k: ("no_diff", {}),
        )
        self.assertIsNone(err)

    def test_empty_baselines_still_fail_gate(self):
        err = check_baseline_freshness(
            {},
            [],
            rollback_semantics=False,
            extract_field_values=lambda *a, **k: ("no_diff", {}),
        )
        self.assertIn("no usable baseline", err or "")


if __name__ == "__main__":
    unittest.main()
