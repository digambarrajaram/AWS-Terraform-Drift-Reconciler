"""Plan JSON destroy/replace detection."""
import unittest

from drift_reconciler.plan_analysis import plan_destructive_changes, plan_risk_summary


class PlanDestructiveChangesTests(unittest.TestCase):
    def test_detects_replace_via_delete_action(self):
        plan = {
            "resource_changes": [{
                "address": "aws_instance.web",
                "change": {
                    "actions": ["delete", "create"],
                    "replace_paths": [["ami"]],
                },
            }],
        }
        risks = plan_destructive_changes(plan)
        self.assertEqual(len(risks), 1)
        self.assertEqual(risks[0]["address"], "aws_instance.web")
        self.assertEqual(risks[0]["replace_paths"], [["ami"]])

    def test_ignores_in_place_update(self):
        plan = {
            "resource_changes": [{
                "address": "aws_instance.web",
                "change": {"actions": ["update"], "replace_paths": []},
            }],
        }
        self.assertEqual(plan_destructive_changes(plan), [])

    def test_summary_flag(self):
        plan = {
            "resource_changes": [{
                "address": "aws_instance.web",
                "change": {"actions": ["delete", "create"], "replace_paths": [["ami"]]},
            }],
        }
        summary = plan_risk_summary(plan)
        self.assertTrue(summary["has_destroy_or_replace"])
        self.assertEqual(summary["resources"][0]["address"], "aws_instance.web")


if __name__ == "__main__":
    unittest.main()
