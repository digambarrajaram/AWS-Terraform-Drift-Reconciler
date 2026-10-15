"""Human-readable rollback plan_output for whole-file security baselines."""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

from drift_baseline import (  # noqa: E402
    TF_FILE_FIELD,
    format_rollback_plan_output,
    tf_file_baseline,
)


class FormatRollbackPlanOutputTests(unittest.TestCase):
    def test_tf_file_shows_unified_diff_not_json(self):
        before = (
            'resource "aws_ecr_repository" "trivy_e2e_mutable_tags" {\n'
            '  image_tag_mutability = "IMMUTABLE"\n'
            "}\n"
        )
        after = (
            'resource "aws_ecr_repository" "trivy_e2e_mutable_tags" {\n'
            '  image_tag_mutability = "MUTABLE"\n'
            "}\n"
        )
        reversed_changes = tf_file_baseline(before, after)
        text = format_rollback_plan_output(
            reversed_changes,
            file_path="terraform_code/foo/trivy-e2e.tf",
        )
        self.assertNotIn('"reversed_changes"', text)
        self.assertNotIn("\\n", text)
        self.assertIn("--- a/trivy-e2e.tf", text)
        self.assertIn('image_tag_mutability = "IMMUTABLE"', text)
        self.assertIn('image_tag_mutability = "MUTABLE"', text)
        self.assertNotIn(TF_FILE_FIELD, text)


if __name__ == "__main__":
    unittest.main()
