"""Failed merge+apply must revert the merge and roll back PR-tied exceptions.

Run: python -m unittest tests.test_apply_failure_recovery
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

from drift_reconciler.apply_flow import _recover_failed_approve_apply  # noqa: E402
from dashboard import serve  # noqa: E402


class _Resp:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.text = "[]"

    def json(self):
        return self.data


class RecoverFailedApproveApplyTests(unittest.TestCase):
    def test_revert_path_marks_manual_revert_without_git_revert(self):
        finished = []

        def _finish(status, result):
            finished.append((status, result))

        with patch("drift_history.mark_reverted") as mark:
            _recover_failed_approve_apply(
                "/tmp/tf", 9, "scope-a", {}, "gate blocked", True, _finish,
            )
        self.assertEqual(finished[0][0], "failed")
        mark.assert_called_once()
        self.assertEqual(mark.call_args.kwargs.get("status"), "manual_revert_required")

    def test_approve_failure_reverts_merge_and_rolls_back_exceptions(self):
        finished = []
        patched_urls = []

        def _finish(status, result):
            finished.append((status, result))

        def fake_patch(url, **kwargs):
            patched_urls.append(url)
            r = type("R", (), {})()
            r.status_code = 204
            return r

        env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        real_get, real_patch = serve.requests.get, serve.requests.patch
        serve.requests.get = lambda *a, **k: _Resp([{
            "fixes_jsonb": [
                {"resource_address": "aws_ecr_repository.trivy_e2e_mutable_tags",
                 "rule_id": "AWS-0033"},
            ],
        }])
        serve.requests.patch = fake_patch
        try:
            with patch(
                "drift_reconciler.apply_flow._revert_on_gate_failure",
                return_value={"status": "reverted_gate_blocked", "result": {"reverted": True}},
            ) as rev, patch(
                "drift_history.reopen_pr_events",
            ) as reopen, patch(
                "drift_history.mark_reverted",
            ):
                _recover_failed_approve_apply(
                    "/tmp/tf", 42, "scope-a", {"repo_branch": "main"},
                    "state checksum mismatch", False, _finish,
                )
            rev.assert_called_once()
            reopen.assert_called_once()
        finally:
            serve.requests.get, serve.requests.patch = real_get, real_patch
            os.environ.clear()
            os.environ.update(env)

        self.assertEqual(finished[0][0], "reverted_gate_blocked")
        self.assertTrue(any("drift_exception_registry" in u for u in patched_urls))


if __name__ == "__main__":
    unittest.main()
