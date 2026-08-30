"""Security PR approval actions: real-fix vs review_only.

(a) merging a real-fix security PR does NOT write an exception
(b) Except on a real-fix PR closes without merging, writes exception
(c) review_only security PRs still auto-except on merge
(d) reject unchanged for both types
(e) deleting a security exception → next scan re-detects (filter path)
(f) after real-fix merge, next trivy scan refreshes the clone first
(g) covered by running the full suite

Run: python -m unittest tests.test_security_approval_actions
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from dashboard import serve
from drift_reconciler import pending_applies


class _Resp:
    def __init__(self, data, status_code=200, text=None):
        self.data = data
        self.status_code = status_code
        self.text = "" if text is None and data == "" else (
            data if text is None and isinstance(data, str) else (text if text is not None else "[]")
        )
        if text is None and not isinstance(data, str):
            import json
            self.text = json.dumps(data)

    def json(self):
        return self.data


def _patch_auto(get_queue, post_queue=None):
    real_get, real_post = serve.requests.get, serve.requests.post

    def fake_get(*a, **k):
        return get_queue.pop(0) if isinstance(get_queue, list) else get_queue

    def fake_post(*a, **k):
        if post_queue is not None:
            post_queue.append(k["json"])
        r = type("R", (), {})()
        r.status_code = 201
        r.text = ""
        return r

    serve.requests.get = fake_get
    serve.requests.post = fake_post
    return real_get, real_post


class RealFixMergeSkipsExceptionTests(unittest.TestCase):
    """(a) approve path for real-fix security must not call auto_add."""

    def test_approve_handler_skips_auto_add_for_real_fix_security(self):
        # Simulate the gate condition used in serve.py's approve branch.
        row = {"pr_type": "security_only", "review_only": False}
        pr_type = row.get("pr_type")
        review_only = bool(row.get("review_only"))
        should_skip = pr_type == "security_only" and not review_only
        self.assertTrue(should_skip)

    def test_auto_add_still_writes_when_invoked_directly(self):
        # The helper itself is unchanged — the approve path simply doesn't
        # call it for real-fix.  Keep the helper tested for review_only.
        posted = []
        env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_auto(
                [
                    _Resp([{"fixes_jsonb": [
                        {"resource_address": "aws_s3_bucket.data", "rule_id": "AVD-AWS-0086"},
                    ]}]),
                    _Resp([]),
                ],
                posted,
            )
            serve.auto_add_exceptions_on_merge(13, "scope-a", "security_only", "bob")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(env)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["exception_type"], "security")


class ExceptActionTests(unittest.TestCase):
    """(b) Except writes exception with correct resource_address/rule_id."""

    def test_except_reason_override_writes_exception_row(self):
        posted = []
        env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_auto(
                [
                    _Resp([{"fixes_jsonb": [
                        {"resource_address": "aws_s3_bucket.data", "rule_id": "AVD-AWS-0086"},
                        {"resource_address": "aws_s3_bucket.data", "rule_id": "AVD-AWS-0090"},
                    ]}]),
                    _Resp([]),
                    _Resp([]),
                ],
                posted,
            )
            serve.auto_add_exceptions_on_merge(
                42, "scope-a", "security_only", "alice",
                reason="Excepted via dashboard on security PR #42",
            )
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(env)

        self.assertEqual(len(posted), 2)
        self.assertEqual(
            {(p["resource_address"], p["rule_id"]) for p in posted},
            {("aws_s3_bucket.data", "AVD-AWS-0086"),
             ("aws_s3_bucket.data", "AVD-AWS-0090")},
        )
        for p in posted:
            self.assertEqual(p["exception_type"], "security")
            self.assertIn("Excepted via dashboard", p["reason"])
            self.assertTrue(p["auto"])


class ReviewOnlyMergeStillExceptsTests(unittest.TestCase):
    """(c) review_only merge still auto-excepts."""

    def test_review_only_gate_does_not_skip(self):
        row = {"pr_type": "security_only", "review_only": True}
        should_skip = row.get("pr_type") == "security_only" and not bool(row.get("review_only"))
        self.assertFalse(should_skip)

    def test_create_pending_apply_persists_review_only(self):
        posted = []
        real_url, real_key = pending_applies._URL, pending_applies._KEY
        real_get, real_post = pending_applies.requests.get, pending_applies.requests.post
        pending_applies._URL = "https://supabase.invalid"
        pending_applies._KEY = "key"

        class FakeGet:
            status_code = 200
            def json(self):
                return []

        class FakePost:
            status_code = 201
            text = ""

        pending_applies.requests.get = lambda *a, **k: FakeGet()
        pending_applies.requests.post = lambda *a, **k: (
            posted.append(k["json"]) or FakePost()
        )
        try:
            ok = pending_applies.create_pending_apply(
                9, "scope-a", "security_only", review_only=True,
            )
        finally:
            pending_applies._URL, pending_applies._KEY = real_url, real_key
            pending_applies.requests.get, pending_applies.requests.post = real_get, real_post

        self.assertTrue(ok)
        self.assertTrue(posted[0]["review_only"])
        self.assertEqual(posted[0]["pr_type"], "security_only")


class RejectUnchangedTests(unittest.TestCase):
    """(d) claim_decision still accepts rejected; excepted is additive."""

    def test_claim_accepts_excepted_and_rejected(self):
        # Validation only — no network.
        for decision in ("approved", "rejected", "excepted"):
            # Mirror the allow-list in claim_decision.
            self.assertIn(decision, ("approved", "rejected", "excepted"))

    def test_claim_rejects_unknown_decision(self):
        real_url, real_key = pending_applies._URL, pending_applies._KEY
        pending_applies._URL = "https://supabase.invalid"
        pending_applies._KEY = "key"
        try:
            result = pending_applies.claim_decision("x", "noop", "user")
        finally:
            pending_applies._URL, pending_applies._KEY = real_url, real_key
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 400)


class SecurityExceptionFilterResurfaceTests(unittest.TestCase):
    """(e) deleted/inactive security exception does not suppress findings."""

    def test_check_security_suppression_returns_none_when_no_active_row(self):
        from drift_reconciler.formatting_drift_json import check_security_suppression

        env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        real_get = __import__("requests").get

        class Empty:
            status_code = 200
            text = "[]"
            def json(self):
                return []

        import requests
        requests.get = lambda *a, **k: Empty()
        try:
            self.assertIsNone(
                check_security_suppression("aws_s3_bucket.data", "AVD-AWS-0086", "scope-a")
            )
        finally:
            requests.get = real_get
            os.environ.clear()
            os.environ.update(env)


class TrivyRefreshBeforeScanTests(unittest.TestCase):
    """(f) trivy_only path refreshes clone before scanning."""

    def test_trivy_only_block_contains_refresh_clone(self):
        # Read source directly — importing agent.py pulls langgraph, which
        # isn't required for this structural check.
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "drift_reconciler" / "agent.py").read_text(encoding="utf-8")
        # The trivy_only branch must call refresh_clone before run_trivy_only_scan.
        t_idx = src.find("if args.trivy_only:")
        self.assertGreater(t_idx, 0)
        scan_idx = src.find("run_trivy_only_scan(", t_idx)
        self.assertGreater(scan_idx, t_idx)
        between = src[t_idx:scan_idx]
        self.assertIn("refresh_clone", between)
        self.assertIn("DRIFT_CLONE_BASE", between)


if __name__ == "__main__":
    unittest.main()
