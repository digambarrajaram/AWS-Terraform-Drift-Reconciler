"""Check the auto-exception policy:
- merging an unmanaged PR adds an unmanaged exception row per drift_events
  resource_id (type + pattern split, auto=True) — resolved rows included
  (the merged-PR webhook resolves rows at merge time, racing this query)
- merging a security PR adds one security exception per recorded
  (resource_address, rule_id) pair
- existing exceptions are not duplicated; non-(unmanaged/security)
  pr_types do nothing; set_security_fixes persists pairs onto the
  awaiting_approval row

Run: python -m unittest tests.test_auto_exceptions
"""
import os
import unittest

from dashboard import serve
from drift_reconciler import pending_applies


def _patch_requests(get_queue, post_queue=None):
    """Patch serve.requests.get/post to serve canned responses per call."""
    real_get, real_post = serve.requests.get, serve.requests.post

    def fake_get(*a, **k):
        return get_queue.pop(0) if isinstance(get_queue, list) else get_queue

    def fake_post(*a, **k):
        if post_queue is not None:
            post_queue.append(k["json"])
        resp = type("R", (), {})()
        resp.status_code = 201
        resp.text = ""
        return resp

    serve.requests.get = fake_get
    serve.requests.post = fake_post
    return real_get, real_post


class _Resp:
    def __init__(self, data, status_code=200, text=None):
        self.data = data
        self.status_code = status_code
        self.text = data if text is None else text

    def json(self):
        return self.data


def _owner_lookup_resp(user_id="00000000-0000-0000-0000-000000000001"):
    return _Resp([{"user_id": user_id}])


class UnmanagedMergeTests(unittest.TestCase):
    def test_merge_adds_exception_per_open_resource(self):
        posted = []
        real_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_requests(
                [_owner_lookup_resp(),
                 _Resp([{"resource_id": "aws_instance.prod-web"},
                        {"resource_id": "aws_s3_bucket.data-bucket"}]),  # drift_events
                 _Resp([]),  # registry dedup — row 1
                 _Resp([])],  # registry dedup — row 2
                posted,
            )
            serve.auto_add_exceptions_on_merge(12, "scope-a", "unmanaged", "alice")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(real_env)

        self.assertEqual(len(posted), 2)
        rows = {p["resource_type"]: p for p in posted}
        self.assertEqual(rows["aws_instance"]["resource_id_pattern"], "prod-web")
        self.assertEqual(rows["aws_s3_bucket"]["resource_id_pattern"], "data-bucket")
        for p in posted:
            self.assertEqual(p["scope"], "scope-a")
            self.assertEqual(p["exception_type"], "unmanaged")
            self.assertEqual(p["approved_by"], "alice")
            self.assertTrue(p["auto"])

    def test_resolved_rows_still_excepted(self):
        # pr=57 bug: drift_events has no `scope` column (it's `account`),
        # so the old filter query 400'd and silently yielded [] → no
        # exceptions written; also, the merged-PR webhook resolves rows at
        # merge time, so a status filter must not exclude them.
        posted, urls = [], []
        real_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_requests(
                [_owner_lookup_resp(),
                 _Resp([{"resource_id": "aws_security_group.launch-wizard-1",
                         "status": "resolved"}]),  # drift_events — already resolved
                 _Resp([])],  # registry dedup
                posted,
            )
            fake_get = serve.requests.get
            serve.requests.get = lambda *a, **k: (urls.append(a[0]) or
                                                  fake_get(*a, **k))
            serve.auto_add_exceptions_on_merge(57, "prod-cra", "unmanaged", "digambar")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(real_env)

        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["resource_type"], "aws_security_group")
        self.assertEqual(posted[0]["resource_id_pattern"], "launch-wizard-1")
        drift_urls = [u for u in urls if "drift_events" in u]
        self.assertTrue(drift_urls)
        self.assertIn("account=eq.prod-cra", drift_urls[0])
        self.assertNotIn("status=eq", drift_urls[0])

    def test_existing_exception_not_duplicated(self):
        posted = []
        real_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_requests(
                [_owner_lookup_resp(),
                 _Resp([{"resource_id": "aws_instance.prod-web"}]),  # drift_events
                 _Resp([{"id": "already-there"}])],  # registry dedup — hit
                posted,
            )
            serve.auto_add_exceptions_on_merge(12, "scope-a", "unmanaged", "alice")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(real_env)

        self.assertEqual(posted, [])


class SecurityMergeTests(unittest.TestCase):
    def test_merge_adds_exception_per_fix_pair(self):
        posted = []
        real_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        try:
            real_get, real_post = _patch_requests(
                [_owner_lookup_resp(),
                 _Resp([{"fixes_jsonb": [
                    {"resource_address": "aws_s3_bucket.data", "rule_id": "AVD-AWS-0086"},
                    {"resource_address": "aws_s3_bucket.data", "rule_id": "AVD-AWS-0090"},
                ]}]),  # pending_applies fixes
                 _Resp([]),  # registry dedup — pair 1
                 _Resp([])],  # registry dedup — pair 2
                posted,
            )
            serve.auto_add_exceptions_on_merge(13, "scope-a", "security_only", "bob")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post
            os.environ.clear()
            os.environ.update(real_env)

        self.assertEqual(len(posted), 2)
        self.assertEqual(
            {(p["resource_address"], p["rule_id"]) for p in posted},
            {("aws_s3_bucket.data", "AVD-AWS-0086"),
             ("aws_s3_bucket.data", "AVD-AWS-0090")},
        )
        for p in posted:
            self.assertEqual(p["exception_type"], "security")
            self.assertEqual(p["approved_by"], "bob")

    def test_drift_pr_type_does_nothing(self):
        real_get, real_post = serve.requests.get, serve.requests.post
        serve.requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no GET expected"))
        serve.requests.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no POST expected"))
        try:
            serve.auto_add_exceptions_on_merge(14, "scope-a", "fix", "carol")
        finally:
            serve.requests.get, serve.requests.post = real_get, real_post


class SetSecurityFixesTests(unittest.TestCase):
    def test_persists_pairs_on_awaiting_approval_row(self):
        patched: list[tuple] = []
        real_url, real_key = pending_applies._URL, pending_applies._KEY
        real_patch = pending_applies.requests.patch
        pending_applies._URL, pending_applies._KEY = "https://supabase.invalid", "key"

        class FakeResp:
            status_code = 204

        pending_applies.requests.patch = lambda *a, **k: patched.append((a[0], k["json"])) or FakeResp()
        try:
            ok = pending_applies.set_security_fixes(13, "scope-a",
                                                    [{"resource_address": "aws_s3_bucket.data",
                                                      "rule_id": "AVD-AWS-0086"}])
        finally:
            pending_applies._URL, pending_applies._KEY = real_url, real_key
            pending_applies.requests.patch = real_patch

        self.assertTrue(ok)
        self.assertIn("status=eq.awaiting_approval", patched[0][0])
        self.assertEqual(patched[0][1]["fixes_jsonb"][0]["rule_id"], "AVD-AWS-0086")


if __name__ == "__main__":
    unittest.main()
