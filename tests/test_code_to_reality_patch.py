"""code_to_reality patches HCL to plan ``before`` (live/state), not ``after`` (config)."""
import os
import sys
import tempfile
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import github_integration as gi  # noqa: E402


class CodeToRealityPatchTests(unittest.TestCase):
    def test_apply_uses_before_for_tags(self):
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "main.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_instance" "web" {\n'
                    '  tags = { Name = "WebServer" }\n'
                    "}\n"
                )
            changes = {
                "tags": {
                    "before": {"Name": "WebServer", "Environment": "prod"},
                    "after": {"Name": "WebServer"},
                },
            }
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf, "aws_instance.web", changes, value_key="before",
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertIn("Environment", out)
            self.assertIn("prod", out)

    def test_filter_drops_tags_all(self):
        filtered = gi.filter_patchable_changes(
            {
                "tags": {"before": {"Name": "a"}, "after": {"Name": "b"}},
                "tags_all": {"before": {}, "after": {}},
            },
        )
        self.assertEqual(list(filtered), ["tags"])

    def test_apply_changes_never_writes_tags_all(self):
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "main.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_instance" "web" {\n'
                    '  tags = { Name = "WebServer" }\n'
                    "}\n"
                )
            changes = {
                "tags": {
                    "before": {"Name": "WebServer", "Environment": "prod"},
                    "after": {"Name": "WebServer"},
                },
                "tags_all": {
                    "before": {"Name": "WebServer", "Environment": "prod"},
                    "after": {"Name": "WebServer"},
                },
            }
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf, "aws_instance.web", changes, value_key="before",
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertNotIn("tags_all", out)


if __name__ == "__main__":
    unittest.main()
