"""Identical report on main must still reopen Approvals after un-exception.

For drift-reports/*.md we stamp a re-review marker so the commit has a
real diff (GitHub empty-tree PRs are skipped).  For HCL (.tf) patches the
empty skip remains.

Also covers the agent path: no active exception + no open PR → pending_applies.

Run: python -m unittest tests.test_empty_identical_pr
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


class _FakeContent:
    def __init__(self, text: str, sha: str = "blobsha"):
        self.sha = sha
        self.decoded_content = text.encode("utf-8")


class _FakeRepo:
    def __init__(self, existing_text: str | None):
        self.existing_text = existing_text
        self.file_writes = 0
        self.pulls = 0
        self.refs_created = 0
        self.refs_deleted = 0
        self.last_write_content = None

    def get_pulls(self, state="open", base=None):
        return []

    def get_git_ref(self, ref):
        if ref.startswith("heads/drift-fix/"):
            self.refs_deleted += 1
            return type("R", (), {"delete": lambda self=None: None})()
        return type("R", (), {"object": type("O", (), {"sha": "abc123"})})()

    def create_git_ref(self, **kw):
        self.refs_created += 1
        return None

    def get_contents(self, path, ref=None):
        if self.existing_text is None:
            raise UnknownObjectException(404, "not found", {})
        return _FakeContent(self.existing_text)

    def create_file(self, **kw):
        self.file_writes += 1
        self.last_write_content = kw.get("content")
        return None

    def update_file(self, **kw):
        self.file_writes += 1
        self.last_write_content = kw.get("content")
        return None

    def create_pull(self, **kw):
        self.pulls += 1
        return _FakePR()


class EmptyIdenticalPrTests(unittest.TestCase):
    def setUp(self):
        self._orig = (gi._resolve_github_client, gi.drift_history.append_entry)
        gi.drift_history.append_entry = lambda **kw: None

    def tearDown(self):
        gi._resolve_github_client, gi.drift_history.append_entry = self._orig

    def _wire(self, repo):
        gi._resolve_github_client = lambda account_label=None: (object(), repo, "main")

    def test_identical_report_stamps_and_opens_pr(self):
        """Merged report still on main + un-excepted rescan → new PR."""
        content = "# Unmanaged resource: aws_security_group.launch-wizard-1\n\nsame\n"
        repo = _FakeRepo(existing_text=content)
        self._wire(repo)
        pr = gi.create_drift_pr(
            resource_id="aws_security_group.launch-wizard-1",
            pr_title="Unmanaged resource: aws_security_group.launch-wizard-1 [MEDIUM]",
            drift_summary="s",
            plan_output="{}",
            file_path="drift-reports/vpc/aws_security_group-launch-wizard-1.md",
            file_content=content,
            account_label="vpc",
            unmanaged=True,
        )
        self.assertIsNotNone(pr)
        self.assertEqual(repo.file_writes, 1)
        self.assertEqual(repo.pulls, 1)
        self.assertIn("re-review", repo.last_write_content)
        self.assertNotEqual(repo.last_write_content, content)

    def test_identical_hcl_still_skips_empty_pr(self):
        content = 'resource "aws_s3_bucket" "foo" {}\n'
        repo = _FakeRepo(existing_text=content)
        self._wire(repo)
        pr = gi.create_drift_pr(
            resource_id="aws_s3_bucket.foo",
            pr_title="Drift fix: aws_s3_bucket.foo [MEDIUM]",
            drift_summary="s",
            plan_output="{}",
            file_path="modules/s3/main.tf",
            file_content=content,
            account_label="vpc",
        )
        self.assertIsNone(pr)
        self.assertEqual(repo.file_writes, 0)
        self.assertEqual(repo.pulls, 0)

    def test_changed_existing_content_updates_and_opens_pr(self):
        repo = _FakeRepo(existing_text="old report")
        self._wire(repo)
        pr = gi.create_drift_pr(
            resource_id="aws_s3_bucket.foo",
            pr_title="Unmanaged resource: aws_s3_bucket.foo [MEDIUM]",
            drift_summary="s",
            plan_output="{}",
            file_path="drift-reports/vpc/aws_s3_bucket-foo.md",
            file_content="new report",
            account_label="vpc",
            unmanaged=True,
        )
        self.assertIsNotNone(pr)
        self.assertEqual(repo.file_writes, 1)
        self.assertEqual(repo.pulls, 1)

    def test_missing_file_creates_and_opens_pr(self):
        repo = _FakeRepo(existing_text=None)
        self._wire(repo)
        pr = gi.create_drift_pr(
            resource_id="aws_s3_bucket.foo",
            pr_title="Unmanaged resource: aws_s3_bucket.foo [MEDIUM]",
            drift_summary="s",
            plan_output="{}",
            file_path="drift-reports/vpc/aws_s3_bucket-foo.md",
            file_content="new report",
            account_label="vpc",
            unmanaged=True,
        )
        self.assertIsNotNone(pr)
        self.assertEqual(repo.file_writes, 1)
        self.assertEqual(repo.pulls, 1)


class UnexceptedRescanCreatesPrTests(unittest.TestCase):
    """No active exception + no open PR → PR + pending_applies entry."""

    def setUp(self):
        import agent
        from drift_reconciler import pending_applies
        self.agent = agent
        self.pending_applies = pending_applies
        self._orig = {
            "account": agent._account_label,
            "pr": agent.gi.create_drift_pr_for_mode,
            "exc": agent.unmanaged_scanner._load_exceptions,
            "open": agent.drift_history.get_open_event,
            "stage": agent.report_stage,
            "pa": pending_applies.create_pending_apply,
        }
        agent._account_label = "dev"
        agent.report_stage = lambda *a, **k: None
        agent.drift_history.get_open_event = lambda *a, **k: None
        agent.unmanaged_scanner._load_exceptions = lambda scope: []
        self.pending = []
        pending_applies.create_pending_apply = (
            lambda *a, **k: self.pending.append(a) or True
        )

    def tearDown(self):
        a, pa = self.agent, self.pending_applies
        a._account_label = self._orig["account"]
        a.gi.create_drift_pr_for_mode = self._orig["pr"]
        a.unmanaged_scanner._load_exceptions = self._orig["exc"]
        a.drift_history.get_open_event = self._orig["open"]
        a.report_stage = self._orig["stage"]
        pa.create_pending_apply = self._orig["pa"]

    def test_previously_reported_unexcepted_resource_creates_pr_and_pending(self):
        class _PR:
            number = 101
            html_url = "https://fake.example/pr/101"

        self.agent.gi.create_drift_pr_for_mode = (
            lambda finding, mode, account_label=None: _PR()
        )
        finding = {
            "resource_id": "aws_security_group.launch-wizard-1",
            "risk_level": "MEDIUM",
            "drift_summary": "untracked",
            "plan_output": "{}",
            "file_path": None,
            "status": "unmanaged",
        }
        result = self.agent.drift_pr_from_finding(
            {"drift_detected": True, "drift_findings": [finding]},
        )
        self.assertEqual(len(result["pr_urls"]), 1)
        self.assertEqual(self.pending, [(101, "dev", "unmanaged")])


if __name__ == "__main__":
    unittest.main()
