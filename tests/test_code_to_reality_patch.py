"""code_to_reality patches HCL to drift ``after`` (live), not ``before`` (IaC)."""
import os
import sys
import tempfile
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import github_integration as gi  # noqa: E402


class CodeToRealityPatchTests(unittest.TestCase):
    def test_apply_uses_after_for_tags(self):
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "main.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_instance" "web" {\n'
                    '  tags = { Name = "WebServer" }\n'
                    "}\n"
                )
            # Drift convention: before=IaC, after=live
            changes = {
                "tags": {
                    "before": {"Name": "WebServer"},
                    "after": {"Name": "WebServer", "Environment": "prod"},
                },
            }
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf, "aws_instance.web", changes, value_key="after",
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
                    "before": {"Name": "WebServer"},
                    "after": {"Name": "WebServer", "Environment": "prod"},
                },
                "tags_all": {
                    "before": {"Name": "WebServer"},
                    "after": {"Name": "WebServer", "Environment": "prod"},
                },
            }
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf, "aws_instance.web", changes, value_key="after",
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertNotIn("tags_all", out)

    def test_filter_drops_last_modified(self):
        filtered = gi.filter_patchable_changes(
            {
                "layers": {
                    "before": [],
                    "after": ["arn:aws:lambda:us-east-1:1:layer:x:1"],
                },
                "last_modified": {
                    "before": "2026-09-30T12:07:33.000+0000",
                    "after": "2026-09-30T12:09:48.000+0000",
                },
            },
        )
        self.assertEqual(list(filtered), ["layers"])

    def test_hcledit_appends_missing_live_only_attribute(self):
        """Live-only drift (e.g. Lambda layers) must append, not no-op set."""
        if not gi.is_hcledit_available():
            self.skipTest("hcledit not on PATH")
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "lambda.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_lambda_function" "hello" {\n'
                    '  function_name = "account-a-hello"\n'
                    '  runtime       = "python3.12"\n'
                    "}\n"
                )
            layer = "arn:aws:lambda:us-east-1:211125607513:layer:aws-fis-extension-x86_64:71"
            changes = {
                "layers": {"before": [], "after": [layer]},
                "architectures": {"before": ["x86_64"], "after": ["arm64"]},
            }
            out = gi.apply_changes_to_file(
                tf, "aws_lambda_function.hello", changes, value_key="after",
            )
            self.assertIn("layers", out)
            self.assertIn(layer, out)
            self.assertIn("architectures", out)
            self.assertIn("arm64", out)
            self.assertNotEqual(
                out,
                'resource "aws_lambda_function" "hello" {\n'
                '  function_name = "account-a-hello"\n'
                '  runtime       = "python3.12"\n'
                "}\n",
            )

    def test_hcledit_sets_existing_attribute(self):
        if not gi.is_hcledit_available():
            self.skipTest("hcledit not on PATH")
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "lambda.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_lambda_function" "hello" {\n'
                    '  architectures = ["x86_64"]\n'
                    "}\n"
                )
            out = gi.apply_changes_to_file(
                tf,
                "aws_lambda_function.hello",
                {"architectures": {"before": ["x86_64"], "after": ["arm64"]}},
                value_key="after",
            )
            self.assertIn('architectures = ["arm64"]', out)
            self.assertNotIn("x86_64", out)

    def test_regex_appends_missing_attribute(self):
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "lambda.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_lambda_function" "hello" {\n'
                    '  runtime = "python3.12"\n'
                    "}\n"
                )
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf,
                    "aws_lambda_function.hello",
                    {
                        "layers": {
                            "before": [],
                            "after": ["arn:aws:lambda:us-east-1:1:layer:x:1"],
                        },
                    },
                    value_key="after",
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertIn("layers", out)
            self.assertIn("arn:aws:lambda:us-east-1:1:layer:x:1", out)


if __name__ == "__main__":
    unittest.main()
