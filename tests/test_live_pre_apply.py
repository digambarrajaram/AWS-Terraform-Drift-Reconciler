"""Check the live pre-apply drift verification:
- changed_resource_addresses: only non-no-op change actions count
- live_drift_rows: managed rows count only if the resource still appears
  in the plan with a change; unmanaged rows always count; fixed
  resources drop out of the gate

Run: python -m unittest tests.test_live_pre_apply
"""
import unittest

from drift_reconciler import rollback_check as rc


class ChangedResourceAddressesTests(unittest.TestCase):
    def test_only_non_noop_actions_count(self):
        plan = {"resource_changes": [
            {"address": "aws_instance.a", "change": {"actions": ["update"]}},
            {"address": "aws_instance.b", "change": {"actions": ["no-op"]}},
            {"address": "aws_instance.c", "change": {"actions": ["create"]}},
            {"address": "aws_instance.d", "change": {"actions": ["no-op", "update"]}},
        ]}
        self.assertEqual(
            rc.changed_resource_addresses(plan),
            {"aws_instance.a", "aws_instance.c", "aws_instance.d"},
        )

    def test_missing_actions_count_as_noop(self):
        plan = {"resource_changes": [{"address": "aws_instance.x", "change": {}}]}
        self.assertEqual(rc.changed_resource_addresses(plan), set())


class LiveDriftRowsTests(unittest.TestCase):
    def test_managed_rows_verified_against_plan(self):
        rows = [
            {"resource_id": "aws_instance.still_drifted", "unmanaged": False},
            {"resource_id": "aws_instance.fixed", "unmanaged": False},
        ]
        plan = {"resource_changes": [
            {"address": "aws_instance.still_drifted",
             "change": {"actions": ["update"]}},
            {"address": "aws_instance.fixed",
             "change": {"actions": ["no-op"]}},
        ]}
        self.assertEqual(
            [r["resource_id"] for r in rc.live_drift_rows(rows, plan)],
            ["aws_instance.still_drifted"],
        )

    def test_unmanaged_rows_always_count(self):
        rows = [
            {"resource_id": "aws_instance.unmanaged", "unmanaged": True},
            {"resource_id": "aws_instance.fixed", "unmanaged": False},
        ]
        plan = {"resource_changes": [
            {"address": "aws_instance.fixed",
             "change": {"actions": ["no-op"]}},
        ]}
        self.assertEqual(
            [r["resource_id"] for r in rc.live_drift_rows(rows, plan)],
            ["aws_instance.unmanaged"],
        )

    def test_row_absent_from_plan_is_resolved(self):
        rows = [{"resource_id": "aws_instance.gone", "unmanaged": False}]
        self.assertEqual(rc.live_drift_rows(rows, {"resource_changes": []}), [])


if __name__ == "__main__":
    unittest.main()
