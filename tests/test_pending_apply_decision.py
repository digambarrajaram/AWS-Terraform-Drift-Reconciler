"""pending_applies.claim_decision — Approve & Merge claim path.

Approving a genuinely awaiting_approval row succeeds; a second decision
(or any non-pending status) returns a clear "Already handled" message
instead of the old opaque "No awaiting_approval row matched".

Run: python -m unittest tests.test_pending_apply_decision
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from drift_reconciler import pending_applies  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            import json
            self.text = json.dumps(payload)

    def json(self):
        return self._payload


class DecisionClaimMissErrorTests(unittest.TestCase):
    def test_missing_row_is_404(self):
        status, msg, extra = pending_applies.decision_claim_miss_error(None)
        self.assertEqual(status, 404)
        self.assertIn("not found", msg.lower())
        self.assertEqual(extra, {})

    def test_already_applied_is_clear_already_handled(self):
        status, msg, extra = pending_applies.decision_claim_miss_error(
            {"status": "applied", "pr_number": 96},
        )
        self.assertEqual(status, 409)
        self.assertIn("Already handled", msg)
        self.assertIn("applied", msg)
        self.assertEqual(extra.get("current_status"), "applied")

    def test_approved_claim_state_is_already_handled(self):
        status, msg, extra = pending_applies.decision_claim_miss_error(
            {"status": "approved"},
        )
        self.assertEqual(status, 409)
        self.assertIn("Already handled", msg)
        self.assertEqual(extra.get("current_status"), "approved")


class ClaimDecisionTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "url": pending_applies._URL,
            "key": pending_applies._KEY,
            "patch": pending_applies.requests.patch,
            "get": pending_applies.requests.get,
        }
        pending_applies._URL = "https://supabase.invalid"
        pending_applies._KEY = "key"

    def tearDown(self):
        pending_applies._URL = self._orig["url"]
        pending_applies._KEY = self._orig["key"]
        pending_applies.requests.patch = self._orig["patch"]
        pending_applies.requests.get = self._orig["get"]

    def test_pending_row_claim_succeeds(self):
        row = {
            "id": "aaa",
            "pr_number": 99,
            "scope": "dev",
            "status": "approved",
            "pr_type": "unmanaged",
        }
        pending_applies.requests.patch = MagicMock(
            return_value=_Resp(200, [row]),
        )
        pending_applies.requests.get = MagicMock()

        result = pending_applies.claim_decision("aaa", "approved", "dashboard-user")
        self.assertTrue(result["ok"])
        self.assertEqual(result["row"]["pr_number"], 99)
        pending_applies.requests.patch.assert_called_once()
        patch_url = pending_applies.requests.patch.call_args[0][0]
        self.assertIn("id=eq.aaa", patch_url)
        self.assertIn("status=eq.awaiting_approval", patch_url)
        pending_applies.requests.get.assert_not_called()

    def test_already_decided_returns_clear_message(self):
        pending_applies.requests.patch = MagicMock(
            return_value=_Resp(200, []),
        )
        pending_applies.requests.get = MagicMock(
            return_value=_Resp(200, [{
                "id": "bbb",
                "pr_number": 96,
                "scope": "dev",
                "status": "applied",
            }]),
        )

        result = pending_applies.claim_decision("bbb", "approved", "dashboard-user")
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 409)
        self.assertIn("Already handled", result["error"])
        self.assertIn("applied", result["error"])
        self.assertEqual(result.get("current_status"), "applied")

    def test_missing_row_returns_404(self):
        pending_applies.requests.patch = MagicMock(
            return_value=_Resp(200, []),
        )
        pending_applies.requests.get = MagicMock(
            return_value=_Resp(200, []),
        )

        result = pending_applies.claim_decision("missing", "approved", "dashboard-user")
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 404)
        self.assertIn("not found", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
