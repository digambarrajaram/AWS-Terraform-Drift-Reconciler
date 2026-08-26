"""Check that sync_pr_state reconciles open drift_events rows with
GitHub's actual PR state:
- merged           → resolve_entry (drift fix is in main)
- closed (unmerged)→ mark_reverted(manual_revert_required) — never left open
- open / unknown   → no write (unknown is left for the next sweep)
Also checks _pr_is_actually_open keeps its old behavior after the
_fetch_pr_state extraction.

Run: python -m unittest tests.test_pr_state_sync
"""
import unittest

from drift_reconciler import drift_history as dh


class SyncPrStateTests(unittest.TestCase):
    def setUp(self):
        self.states: list[str | None] = []
        self.resolved: list[tuple] = []
        self.reverted: list[tuple] = []
        self._orig = {
            "fetch": dh._fetch_pr_state,
            "resolve": dh.resolve_entry,
            "revert": dh.mark_reverted,
        }
        dh._fetch_pr_state = lambda n, a: self.states.pop(0)
        dh.resolve_entry = lambda n, a, resolution="": self.resolved.append((n, a, resolution))
        dh.mark_reverted = lambda n, a, status="reverted", resolution="": \
            self.reverted.append((n, a, status, resolution))

    def tearDown(self):
        dh._fetch_pr_state = self._orig["fetch"]
        dh.resolve_entry = self._orig["resolve"]
        dh.mark_reverted = self._orig["revert"]

    def test_merged_resolves_rows(self):
        self.states.append("merged")
        self.assertEqual(dh.sync_pr_state(5, "scope-a"), "merged")
        self.assertEqual(len(self.resolved), 1)
        self.assertEqual(self.resolved[0][:2], (5, "scope-a"))
        self.assertEqual(self.reverted, [])

    def test_closed_unmerged_marks_manual_revert_required(self):
        self.states.append("closed")
        self.assertEqual(dh.sync_pr_state(6, "scope-a"), "closed")
        self.assertEqual(len(self.reverted), 1)
        self.assertEqual(self.reverted[0][:3], (6, "scope-a", "manual_revert_required"))
        self.assertEqual(self.resolved, [])

    def test_open_and_unknown_write_nothing(self):
        for state in ("open", None):
            self.states.append(state)
            self.assertEqual(dh.sync_pr_state(7, "scope-a"), state)
        self.assertEqual(self.resolved, [])
        self.assertEqual(self.reverted, [])

    def test_pr_is_actually_open_behavior_preserved(self):
        cases = ((None, True), ("open", True), ("closed", False), ("merged", False))
        for state, expected in cases:
            self.states.append(state)
            self.assertEqual(dh._pr_is_actually_open(1), expected, f"state={state!r}")


if __name__ == "__main__":
    unittest.main()
