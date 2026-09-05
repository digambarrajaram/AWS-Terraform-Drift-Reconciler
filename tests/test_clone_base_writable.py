"""verify_clone_base_writable fails fast with a clear error on unwritable dirs.

Run: python -m unittest tests.test_clone_base_writable
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

from environment_credentials import verify_clone_base_writable  # noqa: E402


class CloneBaseWritableTests(unittest.TestCase):
    def test_writable_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DRIFT_CLONE_BASE"] = tmp
            self.assertEqual(verify_clone_base_writable(), os.path.abspath(tmp))

    def test_unwritable_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DRIFT_CLONE_BASE"] = tmp
            with mock.patch("builtins.open", side_effect=PermissionError("denied")):
                with self.assertRaises(RuntimeError) as ctx:
                    verify_clone_base_writable()
            self.assertIn("not writable", str(ctx.exception))
            self.assertIn(tmp, str(ctx.exception))

    def tearDown(self):
        os.environ.pop("DRIFT_CLONE_BASE", None)


if __name__ == "__main__":
    unittest.main()
