"""Apply job must force-sync drift_events status to match pending_applies.

Bugs covered:
- resolve_entry/mark_reverted only patched status=open, so a stale
  reverted finding left PR Queue saying Reverted while Approvals said
  applied/Accepted
- file-only reject wrote status='rejected' (not a finding vocabulary)

Run: python -m unittest tests.test_status_force_sync
"""
import os
import sys
import unittest
from unittest.mock import patch

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import drift_history  # noqa: E402


class ForceSyncTests(unittest.TestCase):
    def test_resolve_entry_force_skips_open_filter(self):
        with patch.object(drift_history, "_patch", return_value=True) as patch_fn:
            drift_history.resolve_entry(42, "scope-a", "ok", force=True)
        params, data = patch_fn.call_args[0]
        self.assertEqual(params, {"pr_number": 42, "account": "scope-a"})
        self.assertNotIn("status", params)
        self.assertEqual(data["status"], "resolved")

    def test_resolve_entry_default_only_open(self):
        with patch.object(drift_history, "_patch", return_value=True) as patch_fn:
            drift_history.resolve_entry(42, "scope-a", "ok")
        params, _data = patch_fn.call_args[0]
        self.assertEqual(params["status"], "open")
        self.assertEqual(params["account"], "scope-a")

    def test_mark_reverted_force_skips_open_filter(self):
        with patch.object(drift_history, "_patch", return_value=True) as patch_fn:
            drift_history.mark_reverted(7, "scope-a", force=True)
        params, data = patch_fn.call_args[0]
        self.assertEqual(params, {"pr_number": 7, "account": "scope-a"})
        self.assertEqual(data["status"], "reverted")


if __name__ == "__main__":
    unittest.main()
