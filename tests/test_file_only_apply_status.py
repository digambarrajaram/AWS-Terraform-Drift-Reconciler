"""File-only PRs (unmanaged/security) skip terraform in _run_apply, but
the row must still reach a *terminal* status so the dashboard log poller
stops.  Bug: the revert branch wrote status='rejected' — the same value
the decision handler writes when the job starts (the claim) — so the row
looked claim-pending forever and polling never stopped.  Revert must
write 'reverted'.

Run: python -m unittest tests.test_file_only_apply_status
"""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402
# Top-level 'drift_history' — the module object _run_apply's local
# `import drift_history as _dh` binds (a different object than
# drift_reconciler.drift_history).
import drift_history  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402


class FileOnlyApplyStatusTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        self.writes = []
        self._orig = {
            "req_tf": agent._pr_requires_terraform,
            "upd": pending_applies.update_pending_apply,
            "rev": drift_history.mark_reverted,
            "res": drift_history.resolve_entry,
        }
        agent._pr_requires_terraform = lambda pr_number, scope: False
        pending_applies.update_pending_apply = lambda *a, **kw: self.writes.append(kw)
        drift_history.mark_reverted = lambda *a, **k: None
        drift_history.resolve_entry = lambda *a, **k: None

    def tearDown(self):
        agent._pr_requires_terraform = self._orig["req_tf"]
        pending_applies.update_pending_apply = self._orig["upd"]
        drift_history.mark_reverted = self._orig["rev"]
        drift_history.resolve_entry = self._orig["res"]
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_file_only_revert_writes_reverted_not_rejected(self):
        agent._run_apply("/tmp/none", 11, "scope-a", run_id="r1", is_revert=True)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]["status"], "reverted")
        self.assertIn("file-only", self.writes[0]["result"]["output"])

    def test_file_only_apply_writes_applied(self):
        agent._run_apply("/tmp/none", 12, "scope-a", run_id="r2", is_revert=False)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
