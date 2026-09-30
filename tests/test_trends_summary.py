"""Trends summary status buckets must be mutually exclusive and sum to total.

Bugs covered:
- Resolved + Unresolved omitted reverted/suppressed/manual_revert rows still
  counted in Total
- Rollback Events is a pr_type overlay, not a third status bucket

Run: python -m unittest tests.test_trends_summary
"""
import unittest

from dashboard.handler_base import _trends_summary_from_rows


class TrendsSummaryTests(unittest.TestCase):
    def test_status_buckets_sum_to_total(self):
        rows = [
            {"resource_id": "a", "status": "resolved", "pr_type": "fix"},
            {"resource_id": "b", "status": "resolved", "pr_type": "batch"},
            {"resource_id": "c", "status": "open", "pr_type": "fix"},
            {"resource_id": "d", "status": "reverted", "pr_type": "fix"},
            {"resource_id": "e", "status": "manual_revert_required", "pr_type": "fix"},
            {"resource_id": "f", "status": "suppressed", "pr_type": "fix"},
            {"resource_id": "g", "status": "resolved", "pr_type": "rollback"},
            {"resource_id": "h", "status": "reverted", "pr_type": "rollback"},
        ]
        s = _trends_summary_from_rows(rows)
        self.assertEqual(s["total"], 8)
        self.assertEqual(s["resolved"], 3)
        self.assertEqual(s["open"], 1)
        self.assertEqual(s["other"], 4)
        self.assertEqual(s["resolved"] + s["open"] + s["other"], s["total"])
        self.assertEqual(s["rollback"], 2)  # overlay, not exclusive
        self.assertEqual(s["uniqueResources"], 8)
        self.assertEqual(s["other_by_status"]["reverted"], 2)
        self.assertEqual(s["other_by_status"]["manual_revert_required"], 1)
        self.assertEqual(s["other_by_status"]["suppressed"], 1)

    def test_user_reported_shape(self):
        """25 total / 15 resolved / 0 open / 7 rollback → 10 other."""
        rows = (
            [{"resource_id": f"r{i}", "status": "resolved", "pr_type": "fix"} for i in range(12)]
            + [{"resource_id": f"rb{i}", "status": "resolved", "pr_type": "rollback"} for i in range(3)]
            + [{"resource_id": f"rv{i}", "status": "reverted", "pr_type": "fix"} for i in range(3)]
            + [{"resource_id": f"rr{i}", "status": "reverted", "pr_type": "rollback"} for i in range(4)]
            + [{"resource_id": f"m{i}", "status": "manual_revert_required", "pr_type": "fix"} for i in range(3)]
        )
        self.assertEqual(len(rows), 25)
        s = _trends_summary_from_rows(rows)
        self.assertEqual(s["resolved"], 15)
        self.assertEqual(s["open"], 0)
        self.assertEqual(s["other"], 10)
        self.assertEqual(s["rollback"], 7)
        self.assertEqual(s["resolved"] + s["open"] + s["other"], 25)

    def test_empty(self):
        s = _trends_summary_from_rows([])
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["other"], 0)
        self.assertEqual(s["rollback"], 0)


if __name__ == "__main__":
    unittest.main()
