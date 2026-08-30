"""Check create_drift_pr's body heading matches the PR kind — the
user-facing-string audit: a security or unmanaged PR must not say
"Drift detected".  security=True → "Security issue detected",
unmanaged=True → "Unmanaged resource detected", fix/rollback keep
"Drift detected", review_only keeps its own heading and commits a
drift-reports/*.md body (GitHub requires a commit; no .tf change).

Run: python -m unittest tests.test_pr_body_language
"""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import github_integration as gi  # noqa: E402
from github import UnknownObjectException  # noqa: E402


class _FakePR:
    number = 1
    html_url = "https://fake.example/pr/1"

    def add_to_labels(self, *a):
        pass


class _FakeGitRef:
    def __init__(self, sha="abc123"):
        self.object = type("O", (), {"sha": sha})()

    def delete(self):
        return None


class _FakeRepo:
    def __init__(self):
        self.pull_kwargs = None
        self.file_writes = 0
        self.written_paths = []

    def get_pulls(self, state="open", base=None):
        return []

    def get_git_ref(self, ref):
        return _FakeGitRef()

    def create_git_ref(self, **kw):
        return None

    def get_contents(self, path, ref=None):
        raise UnknownObjectException(404, "not found", {})

    def create_file(self, **kw):
        self.file_writes += 1
        self.written_paths.append(kw.get("path"))
        return None

    def update_file(self, **kw):
        self.file_writes += 1
        self.written_paths.append(kw.get("path"))
        return None

    def create_pull(self, **kw):
        self.pull_kwargs = kw
        return _FakePR()


class PrBodyLanguageTests(unittest.TestCase):
    def setUp(self):
        self.repo = _FakeRepo()
        self._orig = (gi._resolve_github_client, gi.drift_history.append_entry)
        gi._resolve_github_client = lambda account_label=None: (object(), self.repo, "main")
        gi.drift_history.append_entry = lambda **kw: None

    def tearDown(self):
        gi._resolve_github_client, gi.drift_history.append_entry = self._orig

    def _create(self, **flags):
        kwargs = {
            "resource_id": "aws_instance.foo",
            "pr_title": "t",
            "drift_summary": "s",
            "plan_output": "p",
            "file_path": "main.tf",
            "file_content": "x",
            "account_label": "prod-cra",
        }
        kwargs.update(flags)
        gi.create_drift_pr(**kwargs)
        return self.repo.pull_kwargs["body"]

    def test_security_body(self):
        body = self._create(security=True)
        self.assertIn("## Security issue detected: `aws_instance.foo`", body)
        self.assertNotIn("Drift detected", body)

    def test_unmanaged_body(self):
        body = self._create(unmanaged=True)
        self.assertIn("## Unmanaged resource detected: `aws_instance.foo`", body)
        self.assertNotIn("Drift detected", body)

    def test_fix_body_unchanged(self):
        body = self._create()
        self.assertIn("## Drift detected: `aws_instance.foo`", body)

    def test_rollback_body_keeps_drift_heading(self):
        body = self._create(is_rollback=True)
        self.assertIn("## Drift detected: `aws_instance.foo`", body)

    def test_review_only_body_and_no_tf_write(self):
        # review_only still needs a drift-reports/*.md commit — GitHub
        # rejects empty head==base PRs.  The report is not a .tf patch.
        body = self._create(
            review_only=True,
            file_path="drift-reports/prod-cra/manual-review.md",
            file_content="# review\n",
        )
        self.assertIn("## Security finding — manual review requested", body)
        self.assertEqual(self.repo.file_writes, 1)
        self.assertTrue(all(
            (p or "").startswith("drift-reports/") or (p or "").endswith(".md")
            for p in self.repo.written_paths
        ))
        self.assertFalse(any((p or "").endswith(".tf") for p in self.repo.written_paths))


if __name__ == "__main__":
    unittest.main()
