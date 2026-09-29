"""None vs {} for tags in plan JSON — drift, patch, and apply gates."""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import github_integration as gi  # noqa: E402
from drift_baseline import plan_field_baseline_token, plan_field_values_equal  # noqa: E402
from formatting_drift_json import flatten_diff  # noqa: E402
from rollback_check import _extract_field_values  # noqa: E402


class PlanFieldCompareTests(unittest.TestCase):
    def test_tags_none_and_empty_dict_equivalent(self):
        self.assertTrue(plan_field_values_equal("tags", None, {}))
        self.assertEqual(plan_field_baseline_token("tags", None), "{}")
        self.assertEqual(plan_field_baseline_token("tags", {}), "{}")

    def test_flatten_diff_ignores_tags_none_vs_empty(self):
        diffs = flatten_diff({"tags": None}, {"tags": {}})
        self.assertEqual(diffs, [])

    def test_filter_patchable_drops_equivalent_tags(self):
        filtered = gi.filter_patchable_changes(
            {"tags": {"before": None, "after": {}}, "memory_size": {"before": 128, "after": 150}},
        )
        self.assertNotIn("tags", filtered)
        self.assertIn("memory_size", filtered)

    def test_extract_field_values_tags_none_token(self):
        plan = {
            "resource_changes": [{
                "address": "aws_lambda_function.hello",
                "change": {
                    "before": {"tags": {}, "memory_size": 150},
                    "after": {"tags": {}, "memory_size": 150},
                    "actions": ["update"],
                },
            }],
        }
        outcome, values = _extract_field_values(
            plan, "aws_lambda_function.hello", ["tags", "memory_size"],
        )
        self.assertEqual(outcome, "no_diff")
        self.assertEqual(values.get("tags"), "{}")


if __name__ == "__main__":
    unittest.main()
