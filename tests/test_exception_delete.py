"""Exception delete/expire: dashboard sends entry.id; backend must match by id.

Bug: frontend POST /api/exceptions with action=delete and entry={id} got
400 "resource_type and resource_id_pattern are required" because
_do_exception_update only knew the composite natural key.

Run: python -m unittest tests.test_exception_delete
"""
import io
import json
import os
import unittest
from unittest.mock import MagicMock

from dashboard import serve


class _Resp:
    def __init__(self, status_code=204, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self._payload


class ExceptionDeleteByIdTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        self.patched = []
        self._orig_patch = serve.requests.patch
        serve.requests.patch = lambda url, **k: (
            self.patched.append((url, k.get("json"))),
            _Resp(204),
        )[1]

    def tearDown(self):
        serve.requests.patch = self._orig_patch
        os.environ.clear()
        os.environ.update(self.old_env)

    def _handler(self):
        h = serve._Handler.__new__(serve._Handler)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._json_error = MagicMock()
        return h

    def test_delete_by_id_succeeds_without_composite_keys(self):
        """Frontend sends only {id} — must soft-delete (active=False)."""
        h = self._handler()
        headers = {
            "apikey": "key",
            "Authorization": "Bearer key",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        h._do_exception_update(
            "dev",
            "unmanaged",
            {"id": "exc-uuid-1"},
            headers,
            "https://supabase.invalid/rest/v1/drift_exception_registry",
            {"active": False},
        )
        self.assertFalse(h._json_error.called)
        self.assertEqual(len(self.patched), 1)
        url, body = self.patched[0]
        self.assertIn("id=eq.exc-uuid-1", url)
        self.assertIn("scope=eq.dev", url)
        self.assertIn("exception_type=eq.unmanaged", url)
        self.assertIn("active=eq.true", url)
        self.assertNotIn("resource_type=", url)
        self.assertEqual(body, {"active": False})
        h.send_response.assert_called_with(200)

    def test_delete_without_id_still_requires_composite_keys(self):
        h = self._handler()
        h._do_exception_update(
            "dev",
            "unmanaged",
            {},  # no id, no type/pattern
            {},
            "https://supabase.invalid/rest/v1/drift_exception_registry",
            {"active": False},
        )
        h._json_error.assert_called()
        status, msg = h._json_error.call_args[0][:2]
        self.assertEqual(status, 400)
        self.assertIn("resource_type and resource_id_pattern", msg)
        self.assertEqual(self.patched, [])

    def test_delete_by_composite_key_still_works(self):
        h = self._handler()
        h._do_exception_update(
            "dev",
            "unmanaged",
            {
                "resource_type": "aws_security_group",
                "resource_id_pattern": "launch-wizard-1",
            },
            {},
            "https://supabase.invalid/rest/v1/drift_exception_registry",
            {"active": False},
        )
        self.assertFalse(h._json_error.called)
        url, _ = self.patched[0]
        self.assertIn("resource_type=eq.aws_security_group", url)
        self.assertIn("resource_id_pattern=eq.launch-wizard-1", url)


if __name__ == "__main__":
    unittest.main()
