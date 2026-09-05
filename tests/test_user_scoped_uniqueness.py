"""Per-user uniqueness and environment resolution isolation.

Verifies owner-scoped identity for environments, pending_applies,
exception registry, and env/ownership resolution when slugs collide
across users.

Run: python -m unittest tests.test_user_scoped_uniqueness
"""
from __future__ import annotations

import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dashboard import env as dashboard_env  # noqa: E402
from dashboard import serve  # noqa: E402
from drift_reconciler import ownership  # noqa: E402
from drift_reconciler import pending_applies  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            import json
            self.text = json.dumps(payload)

    def json(self):
        return self._payload


USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


class OwnerLookupTests(unittest.TestCase):
    def setUp(self):
        self._orig_get = ownership.requests.get

    def tearDown(self):
        ownership.requests.get = self._orig_get

    def test_ambiguous_slug_without_user_returns_none(self):
        """Same slug for two users must not return the first row's owner."""
        ownership.requests.get = lambda url, **kw: _Resp(
            200,
            [{"user_id": USER_A}, {"user_id": USER_B}],
        )
        self.assertIsNone(ownership.owner_user_id_for_scope("prod"))

    def test_unique_slug_without_user_returns_owner(self):
        ownership.requests.get = lambda url, **kw: _Resp(200, [{"user_id": USER_A}])
        self.assertEqual(ownership.owner_user_id_for_scope("prod"), USER_A)

    def test_known_user_filters_by_user_id_and_slug(self):
        captured = {}

        def _get(url, **kw):
            captured["url"] = url
            return _Resp(200, [{"user_id": USER_A}])

        ownership.requests.get = _get
        self.assertEqual(
            ownership.owner_user_id_for_scope("prod", user_id=USER_A),
            USER_A,
        )
        self.assertIn(f"user_id=eq.{USER_A}", captured["url"])


class EnvCacheIsolationTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "cache": dict(dashboard_env._ENV_CACHE),
            "ts": dict(dashboard_env._ENV_CACHE_TS),
            "get": dashboard_env.requests.get,
            "url": os.environ.get("SUPABASE_URL"),
            "key": os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        }
        dashboard_env._ENV_CACHE.clear()
        dashboard_env._ENV_CACHE_TS.clear()
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"

    def tearDown(self):
        dashboard_env._ENV_CACHE.clear()
        dashboard_env._ENV_CACHE_TS.clear()
        dashboard_env._ENV_CACHE.update(self._orig["cache"])
        dashboard_env._ENV_CACHE_TS.update(self._orig["ts"])
        dashboard_env.requests.get = self._orig["get"]
        if self._orig["url"] is None:
            os.environ.pop("SUPABASE_URL", None)
        else:
            os.environ["SUPABASE_URL"] = self._orig["url"]
        if self._orig["key"] is None:
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        else:
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = self._orig["key"]

    def test_env_for_scope_never_returns_wrong_user_when_ambiguous(self):
        now = time.monotonic()
        dashboard_env._ENV_CACHE[dashboard_env._ALL_USERS_KEY] = [
            {"slug": "prod", "user_id": USER_A, "tf_directory_path": "a/"},
            {"slug": "prod", "user_id": USER_B, "tf_directory_path": "b/"},
        ]
        dashboard_env._ENV_CACHE[USER_A] = [
            {"slug": "prod", "user_id": USER_A, "tf_directory_path": "a/"},
        ]
        dashboard_env._ENV_CACHE[USER_B] = [
            {"slug": "prod", "user_id": USER_B, "tf_directory_path": "b/"},
        ]
        dashboard_env._ENV_CACHE_TS[dashboard_env._ALL_USERS_KEY] = now
        dashboard_env._ENV_CACHE_TS[USER_A] = now
        dashboard_env._ENV_CACHE_TS[USER_B] = now

        env_a = dashboard_env._env_for_scope("prod", USER_A)
        env_b = dashboard_env._env_for_scope("prod", USER_B)
        self.assertEqual(env_a["user_id"], USER_A)
        self.assertEqual(env_b["user_id"], USER_B)
        self.assertIsNone(dashboard_env._env_for_scope("prod", None))

    def test_per_user_fetch_includes_user_id_filter(self):
        captured = {}

        def _get(url, **kw):
            captured["url"] = url
            return _Resp(200, [{"slug": "prod", "user_id": USER_A}])

        dashboard_env.requests.get = _get
        dashboard_env._get_active_environments(USER_A)
        self.assertIn(f"user_id=eq.{USER_A}", captured["url"])


class PendingAppliesIdentityTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "url": pending_applies._URL,
            "key": pending_applies._KEY,
            "get": pending_applies.requests.get,
            "post": pending_applies.requests.post,
            "owner": ownership.owner_user_id_for_scope,
        }
        pending_applies._URL = "https://supabase.invalid"
        pending_applies._KEY = "key"
        self.get_urls = []
        self.post_bodies = []

        def _get(url, **kw):
            self.get_urls.append(url)
            return _Resp(200, [])

        def _post(url, **kw):
            self.post_bodies.append(kw.get("json"))
            return _Resp(201, [{"id": "new"}])

        pending_applies.requests.get = _get
        pending_applies.requests.post = _post
        ownership.owner_user_id_for_scope = lambda scope, user_id=None: USER_A

    def tearDown(self):
        pending_applies._URL = self._orig["url"]
        pending_applies._KEY = self._orig["key"]
        pending_applies.requests.get = self._orig["get"]
        pending_applies.requests.post = self._orig["post"]
        ownership.owner_user_id_for_scope = self._orig["owner"]

    def test_create_dedup_includes_user_id(self):
        ok = pending_applies.create_pending_apply(42, "prod", user_id=USER_A)
        self.assertTrue(ok)
        self.assertTrue(any(f"user_id=eq.{USER_A}" in u for u in self.get_urls))
        self.assertEqual(self.post_bodies[0]["user_id"], USER_A)

    def test_update_targets_user_id_pr_scope(self):
        patched = []

        def _patch(url, **kw):
            patched.append(url)
            return _Resp(204)

        pending_applies.requests.patch = _patch
        pending_applies.update_pending_apply(42, "prod", user_id=USER_A, status="applied")
        self.assertEqual(len(patched), 1)
        self.assertIn(f"user_id=eq.{USER_A}", patched[0])
        self.assertIn("pr_number=eq.42", patched[0])
        self.assertIn("scope=eq.prod", patched[0])


class ExceptionPolicyUserFilterTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        self.get_urls = []
        serve.requests.get = lambda url, **kw: (
            self.get_urls.append(url),
            _Resp(200, []),
        )[1]
        serve.requests.post = lambda url, **kw: _Resp(201, [{"id": "x"}])

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_already_exists_filters_by_user_id(self):
        from dashboard.exceptions_policy import auto_add_exceptions_on_merge

        captured = []

        def fake_get(url, **kw):
            captured.append(url)
            if "drift_events" in url:
                return _Resp(200, [{"resource_id": "aws_instance.prod-web"}])
            return _Resp(200, [])

        serve.requests.get = fake_get
        serve.requests.post = lambda url, **kw: _Resp(201, [{"id": "x"}])
        auto_add_exceptions_on_merge(
            1, "prod", "unmanaged", "tester", user_id=USER_A,
        )
        exists_urls = [u for u in captured if "drift_exception_registry" in u and "select=id" in u]
        self.assertTrue(exists_urls)
        self.assertIn(f"user_id=eq.{USER_A}", exists_urls[0])


class ExceptionHandlerUserFilterTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        self.patched = []
        serve.requests.patch = lambda url, **k: (
            self.patched.append(url),
            _Resp(204),
        )[1]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def _handler(self, user_id=USER_A):
        h = serve._Handler.__new__(serve._Handler)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._json_error = MagicMock()
        h.auth_user_id = user_id
        return h

    def test_fetch_and_update_include_user_id(self):
        h = self._handler()
        h._do_exception_update(
            "prod",
            "unmanaged",
            {"id": "exc-1"},
            {},
            "https://supabase.invalid/rest/v1/drift_exception_registry",
            {"active": False},
        )
        self.assertEqual(len(self.patched), 1)
        self.assertIn(f"user_id=eq.{USER_A}", self.patched[0])


class MigrationPrecheckQueriesTests(unittest.TestCase):
    """Document expected zero-violation pre-check queries for migrations."""

    def test_environments_precheck_sql_present(self):
        path = os.path.join(_ROOT, "migrations", "environments_user_id_slug_unique.sql")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("group by user_id, slug", text)
        self.assertIn("having count(*) > 1", text)

    def test_pending_applies_precheck_sql_present(self):
        path = os.path.join(_ROOT, "migrations", "pending_applies_user_id_pr_scope_unique.sql")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("group by user_id, pr_number, scope", text)

    def test_exception_registry_backfill_joins_scope_to_slug(self):
        path = os.path.join(_ROOT, "migrations", "add_user_id_to_exception_registry.sql")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("r.scope = e.slug", text)


if __name__ == "__main__":
    unittest.main()
