"""Check pr_type consistency in the two Supabase tables.

1. A batch PR (create_drift_pr_for_file) must NOT append a spurious
   "fix"-typed row for its synthetic branch_id — only per-finding
   "batch" rows.
2. create_pending_apply must pass pr_type through to the POST body so
   the approval queue can label security/unmanaged PRs.

Run: python tests/test_pr_type_consistency.py
"""
import unittest

import drift_reconciler.github_integration as gi
from drift_reconciler import pending_applies


class FakePR:
    def __init__(self, number: int = 42):
        self.number = number
        self.html_url = f"https://github.com/x/y/pull/{number}"


class GithubBatchTypeTests(unittest.TestCase):
    def test_batch_pr_appends_only_per_finding_batch_rows(self):
        findings = [
            {
                "resource_id": "aws_instance.web-1",
                "risk_level": "HIGH",
                "drift_summary": "tags.Name changed",
                "changes": {"tags": {"Name": {"before": "a", "after": "b"}}},
                "file_path": "main.tf",
                "plan_output": "~ tags",
            },
            {
                "resource_id": "aws_instance.web-2",
                "risk_level": "MEDIUM",
                "drift_summary": "tags.Name changed",
                "changes": {"tags": {"Name": {"before": "a", "after": "c"}}},
                "file_path": "main.tf",
                "plan_output": "~ tags",
            },
        ]

        calls: dict = {}
        def fake_create_drift_pr(**kwargs):
            calls["create_drift_pr"] = kwargs
            return FakePR(42)

        appended: list[dict] = []
        def fake_append(**kwargs):
            appended.append(kwargs)

        real_create = gi.create_drift_pr
        real_append = gi.drift_history.append_entry
        gi.create_drift_pr = fake_create_drift_pr
        gi.drift_history.append_entry = fake_append
        gi._apply_changes_batch = lambda path, fs: "# patched"
        gi.to_repo_relative_path = lambda p: p
        try:
            pr = gi.create_drift_pr_for_file(findings, "code_to_reality", account_label="test")
        finally:
            gi.create_drift_pr = real_create
            gi.drift_history.append_entry = real_append

        self.assertIsNotNone(pr)
        self.assertIs(
            calls["create_drift_pr"]["append_history"], False,
            "batch PR must not get create_drift_pr's own 'fix'-typed row",
        )
        self.assertEqual(len(appended), 2)
        self.assertTrue(all(a["pr_type"] == "batch" for a in appended),
                        f"all appended rows must be 'batch', got: {[a['pr_type'] for a in appended]}")
        self.assertEqual(
            {a["resource_id"] for a in appended},
            {"aws_instance.web-1", "aws_instance.web-2"},
        )


class PendingApplyTypeTests(unittest.TestCase):
    def test_create_pending_apply_passes_pr_type(self):
        posted: list[dict] = []

        class FakeResp:
            status_code = 201

            @staticmethod
            def json():
                return []

        real_url, real_key = pending_applies._URL, pending_applies._KEY
        real_get, real_post = pending_applies.requests.get, pending_applies.requests.post
        pending_applies._URL, pending_applies._KEY = "https://supabase.invalid", "key"
        pending_applies.requests.get = lambda *a, **k: FakeResp()
        pending_applies.requests.post = lambda *a, **k: posted.append(k["json"]) or FakeResp()
        try:
            ok = pending_applies.create_pending_apply(7, "scope-a", "security_only")
        finally:
            pending_applies._URL, pending_applies._KEY = real_url, real_key
            pending_applies.requests.get, pending_applies.requests.post = real_get, real_post

        self.assertTrue(ok)
        self.assertEqual(posted[0]["pr_type"], "security_only")
        self.assertEqual(posted[0]["pr_number"], 7)
        self.assertEqual(posted[0]["scope"], "scope-a")


if __name__ == "__main__":
    unittest.main()
