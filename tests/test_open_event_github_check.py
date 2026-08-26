"""Check get_open_event's dedup never blocks on a stale DB status: the
row's status=open is only a precondition — GitHub's live PR state
decides.  A closed/merged PR must not block a new PR on the next scan
(unless excepted, which the exception filter covers elsewhere); the
stale row gets patched to resolved so later scans skip the GitHub call.

Run: python -m unittest tests.test_open_event_github_check
"""
import os
import unittest

from drift_reconciler import drift_history as dh


class _Resp:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.text = str(data) if data else ""

    def json(self):
        return self.data


class OpenEventGithubCheckTests(unittest.TestCase):
    def setUp(self):
        self.states: list[str | None] = []
        self.patched: list[dict] = []
        self._orig = {
            "url": dh._URL, "key": dh._KEY,
            "fetch": dh._fetch_pr_state,
            "get": dh.requests.get,
            "patch": dh.requests.patch,
        }
        dh._URL, dh._KEY = "https://supabase.invalid", "key"
        dh._fetch_pr_state = lambda n, a: self.states.pop(0)
        dh.requests.get = lambda *a, **k: _Resp([{
            "id": 42, "pr_number": 78, "pr_type": "unmanaged", "status": "open",
        }])
        dh.requests.patch = lambda *a, **k: self.patched.append(k["json"]) or _Resp([], 204)

    def tearDown(self):
        dh._URL, dh._KEY = self._orig["url"], self._orig["key"]
        dh._fetch_pr_state = self._orig["fetch"]
        dh.requests.get = self._orig["get"]
        dh.requests.patch = self._orig["patch"]
        os.environ.pop("SUPABASE_URL", None)

    def test_merged_pr_does_not_block_new_pr(self):
        self.states.append("merged")
        self.assertIsNone(dh.get_open_event("aws_dynamodb_table.terraform-locks",
                                            "pord-kyc", "unmanaged"))
        # stale row patched to resolved so the next scan skips GitHub
        self.assertEqual(len(self.patched), 1)
        self.assertEqual(self.patched[0]["status"], "resolved")

    def test_closed_pr_does_not_block_new_pr(self):
        self.states.append("closed")
        self.assertIsNone(dh.get_open_event("aws_s3_bucket.sec-acc-tf-state",
                                            "pord-kyc", "unmanaged"))
        self.assertEqual(len(self.patched), 1)

    def test_genuinely_open_pr_still_blocks(self):
        self.states.append("open")
        row = dh.get_open_event("aws_security_group.launch-wizard-1",
                                "pord-kyc", "unmanaged")
        self.assertIsNotNone(row)
        self.assertEqual(row["pr_number"], 78)
        self.assertEqual(self.patched, [])  # no PATCH for a truly open PR

    def test_unknown_github_state_blocks_safely(self):
        # No repo/token or network error → None → keep blocking rather
        # than risk a duplicate PR.
        self.states.append(None)
        self.assertIsNotNone(dh.get_open_event("aws_instance.WebServer",
                                               "pord-kyc", "unmanaged"))
        self.assertEqual(self.patched, [])


if __name__ == "__main__":
    unittest.main()
