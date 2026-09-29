"""deleted_externally rollback baselines and apply gates."""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

from drift_baseline import (  # noqa: E402
    changes_for_history,
    deleted_externally_baseline,
    infer_baseline_from_row,
    verify_deleted_externally_plan,
)
from drift_findings import build_drift_findings  # noqa: E402


class DeletedExternallyBaselineTests(unittest.TestCase):
    def test_changes_for_history_uses_sentinel(self):
        finding = {
            "status": "deleted_externally",
            "changes": {},
        }
        self.assertEqual(changes_for_history(finding), deleted_externally_baseline())

    def test_infer_from_drift_summary(self):
        row = {
            "changes_jsonb": None,
            "drift_summary": "Resource was deleted outside of Terraform (found in state, missing from AWS).",
        }
        self.assertEqual(infer_baseline_from_row(row), deleted_externally_baseline())

    def test_verify_revert_expects_create(self):
        plan = {
            "resource_changes": [{
                "address": "aws_lambda_function.hello",
                "change": {"actions": ["create"], "before": None, "after": {}},
            }],
        }
        self.assertIsNone(
            verify_deleted_externally_plan(
                plan, "aws_lambda_function.hello", is_revert=True,
            ),
        )

    def test_verify_revert_rejects_noop(self):
        plan = {
            "resource_changes": [{
                "address": "aws_lambda_function.hello",
                "change": {"actions": ["no-op"], "before": {}, "after": {}},
            }],
        }
        err = verify_deleted_externally_plan(
            plan, "aws_lambda_function.hello", is_revert=True,
        )
        self.assertIsNotNone(err)
        self.assertIn("no-op", err)

    def test_lambda_delete_risk_is_high(self):
        report = {
            "report_type": "drift",
            "resources": [{
                "address": "aws_lambda_function.hello",
                "status": "deleted_externally",
                "changes": {},
                "security_impact": "high",
                "file_path": "lambda.tf",
            }],
        }
        findings = build_drift_findings(report)
        self.assertEqual(findings[0]["risk_level"], "HIGH")
        self.assertIn("__deleted_externally__", findings[0]["changes"])


if __name__ == "__main__":
    unittest.main()
