"""Plain-language exception status lines for unmanaged scans.

Run: python -m unittest tests.test_exception_status_messages
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import unmanaged_scanner as us  # noqa: E402


class ExceptionStatusMessageTests(unittest.TestCase):
    def test_zero_exceptions_summary(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            us.print_exceptions_lookup_summary("vpc", [])
        self.assertIn(
            "No resources are currently excepted for vpc — all findings will be evaluated normally.",
            buf.getvalue(),
        )

    def test_n_exceptions_summary_lists_pairs(self):
        buf = io.StringIO()
        rows = [
            {"resource_type": "aws_instance", "resource_id_pattern": "web"},
            {"resource_type": "aws_s3_bucket", "resource_id_pattern": "logs"},
        ]
        with redirect_stdout(buf):
            us.print_exceptions_lookup_summary("vpc", rows)
        out = buf.getvalue()
        self.assertIn("2 resource(s) excepted for vpc", out)
        self.assertIn("aws_instance/web", out)
        self.assertIn("aws_s3_bucket/logs", out)

    def test_skip_with_attribution(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            us.print_exception_skip(
                "aws_instance.web",
                {"approved_by": "alice", "created_at": "2026-08-20T12:00:00+00:00"},
            )
        self.assertEqual(
            buf.getvalue().strip(),
            "Skipping aws_instance.web: already excepted (added 2026-08-20 by alice)",
        )

    def test_skip_without_attribution(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            us.print_exception_skip("aws_instance.web", {})
        self.assertEqual(
            buf.getvalue().strip(),
            "Skipping aws_instance.web: already excepted",
        )


if __name__ == "__main__":
    unittest.main()
