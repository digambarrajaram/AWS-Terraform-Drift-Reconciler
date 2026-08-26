"""Report markdown paths must be scoped by account/environment.

When multiple environments share one GitHub repo, an unscoped
drift-reports/{resource}.md path lets vpc's merged report make a
first-time `dev` scan skip PR creation via the identical-content
short-circuit — even though `dev` has never scanned that resource.

Run: python -m unittest tests.test_scoped_drift_report_path
"""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import github_integration as gi  # noqa: E402
from github import UnknownObjectException  # noqa: E402


class DriftReportPathTests(unittest.TestCase):
    def test_path_includes_account_and_resource(self):
        self.assertEqual(
            gi.drift_report_repo_path("dev", "aws_security_group.launch-wizard-1"),
            "drift-reports/dev/aws_security_group-launch-wizard-1.md",
        )
        self.assertEqual(
            gi.drift_report_repo_path("vpc", "aws_security_group.launch-wizard-1"),
            "drift-reports/vpc/aws_security_group-launch-wizard-1.md",
        )

    def test_different_accounts_do_not_share_path(self):
        a = gi.drift_report_repo_path("dev", "aws_internet_gateway.igw-07a66a89c4c399df2")
        b = gi.drift_report_repo_path("vpc", "aws_internet_gateway.igw-07a66a89c4c399df2")
        self.assertNotEqual(a, b)


class _FakePR:
    number = 1
    html_url = "https://fake.example/pr/1"

    def add_to_labels(self, *a):
        pass


class _PathAwareRepo:
    """Simulates a shared-repo layout: vpc's report already exists;
    other account paths 404 until written."""

    def __init__(self):
        self.blobs: dict[str, str] = {
            "drift-reports/vpc/aws_security_group-launch-wizard-1.md": (
                "# Unmanaged resource: aws_security_group.launch-wizard-1\n\nvpc copy\n"
            ),
        }
        self.file_writes: list[str] = []
        self.pulls = 0

    def get_pulls(self, state="open", base=None):
        return []

    def get_git_ref(self, ref):
        if ref.startswith("heads/drift-fix/"):
            return type("R", (), {"delete": lambda self=None: None})()
        return type("R", (), {"object": type("O", (), {"sha": "abc123"})})()

    def create_git_ref(self, **kw):
        return None

    def get_contents(self, path, ref=None):
        if path not in self.blobs:
            raise UnknownObjectException(404, "not found", {})
        text = self.blobs[path]
        return type("C", (), {
            "sha": "blob",
            "decoded_content": text.encode("utf-8"),
        })()

    def create_file(self, **kw):
        self.blobs[kw["path"]] = kw["content"]
        self.file_writes.append(kw["path"])
        return None

    def update_file(self, **kw):
        self.blobs[kw["path"]] = kw["content"]
        self.file_writes.append(kw["path"])
        return None

    def create_pull(self, **kw):
        self.pulls += 1
        return _FakePR()


class CrossAccountSkipTests(unittest.TestCase):
    def setUp(self):
        self.repo = _PathAwareRepo()
        self._orig = (gi._resolve_github_client, gi.drift_history.append_entry)
        gi._resolve_github_client = lambda account_label=None: (object(), self.repo, "main")
        gi.drift_history.append_entry = lambda **kw: None

    def tearDown(self):
        gi._resolve_github_client, gi.drift_history.append_entry = self._orig

    def test_dev_does_not_skip_because_vpc_already_has_same_resource_report(self):
        # Same resource_id, shared repo — vpc's blob must not suppress
        # a first-time PR for `dev`.
        content = (
            "# Unmanaged resource: aws_security_group.launch-wizard-1\n\n"
            "dev copy — could even match vpc text\n"
        )
        # Even if content is identical to vpc's report, the PATH differs,
        # so get_contents on the scoped path 404s → create_file → PR.
        finding = {
            "resource_id": "aws_security_group.launch-wizard-1",
            "risk_level": "MEDIUM",
            "drift_summary": "untracked",
            "plan_output": "{}",
            "status": "unmanaged",
            "file_path": None,
            "changes": {},
        }
        # Force identical body to vpc's blob to prove path scoping alone
        # prevents the false skip (not content difference).
        self.repo.blobs["drift-reports/vpc/aws_security_group-launch-wizard-1.md"] = content

        pr = gi.create_drift_pr_for_mode(finding, "code_to_reality", account_label="dev")
        self.assertIsNotNone(pr)
        self.assertEqual(self.repo.pulls, 1)
        self.assertEqual(
            self.repo.file_writes,
            ["drift-reports/dev/aws_security_group-launch-wizard-1.md"],
        )
        # vpc's path untouched; both accounts now have their own files.
        self.assertIn("drift-reports/vpc/aws_security_group-launch-wizard-1.md", self.repo.blobs)
        self.assertIn("drift-reports/dev/aws_security_group-launch-wizard-1.md", self.repo.blobs)


if __name__ == "__main__":
    unittest.main()
