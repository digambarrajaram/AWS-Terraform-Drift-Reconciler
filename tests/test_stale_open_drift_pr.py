"""Open PR dedup must not freeze Approvals on n-1 drift payloads."""
import unittest

from drift_reconciler import drift_history as dh


class OpenEventMatchesFindingTests(unittest.TestCase):
    def test_same_changes_match(self):
        finding = {
            "resource_id": "aws_instance.foo",
            "status": None,
            "changes": {"instance_type": {"before": "t3.micro", "after": "t3.small"}},
        }
        event = {
            "id": "1",
            "pr_number": 10,
            "changes_jsonb": {
                "instance_type": {"before": "t3.micro", "after": "t3.small"},
            },
        }
        self.assertTrue(dh.open_event_matches_finding(event, finding))

    def test_changed_live_drift_is_stale(self):
        finding = {
            "resource_id": "aws_instance.foo",
            "status": None,
            "changes": {"instance_type": {"before": "t3.micro", "after": "t3.large"}},
        }
        event = {
            "id": "1",
            "pr_number": 10,
            "changes_jsonb": {
                "instance_type": {"before": "t3.micro", "after": "t3.small"},
            },
        }
        self.assertFalse(dh.open_event_matches_finding(event, finding))

    def test_missing_stored_baseline_is_stale(self):
        finding = {
            "resource_id": "aws_instance.foo",
            "status": None,
            "changes": {"instance_type": {"before": "t3.micro", "after": "t3.small"}},
        }
        self.assertFalse(dh.open_event_matches_finding({"id": "1", "pr_number": 10}, finding))
        self.assertFalse(dh.open_event_matches_finding(None, finding))


if __name__ == "__main__":
    unittest.main()
