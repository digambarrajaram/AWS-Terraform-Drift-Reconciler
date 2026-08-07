import unittest
from types import SimpleNamespace

from requests.exceptions import ChunkedEncodingError

import drift_reconciler.github_integration as gi


class FakePR:
    def __init__(self, ref: str):
        self.head = SimpleNamespace(ref=ref)
        self.comment_added = False
        self.closed = False

    def create_issue_comment(self, text: str):
        self.comment_added = True

    def edit(self, state: str = "closed"):
        self.closed = True


class FakeRepo:
    def __init__(self):
        self.calls = 0
        self.pr = FakePR("drift-fix/test/aws_instance-foo-123")

    def get_pulls(self, state: str = "open", base: str | None = None):
        self.calls += 1
        if self.calls == 1:
            raise ChunkedEncodingError("Connection broken: IncompleteRead(7165 bytes read, 3075 more expected)")
        return [self.pr]


class GithubIntegrationRetryTests(unittest.TestCase):
    def test_close_superseded_prs_retries_transient_chunked_encoding_error(self):
        repo = FakeRepo()

        gi.close_superseded_prs(
            repo,
            resource_id="aws_instance.foo",
            account_label="test",
            base_branch="main",
            is_rollback=False,
        )

        self.assertEqual(repo.calls, 2)
        self.assertTrue(repo.pr.comment_added)
        self.assertTrue(repo.pr.closed)


if __name__ == "__main__":
    unittest.main()
