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

    def test_filter_drops_lambda_computed_arns(self):
        filtered = gi.filter_patchable_changes(
            {
                "layers": {
                    "before": [],
                    "after": ["arn:aws:lambda:us-east-1:1:layer:x:1"],
                },
                "qualified_arn": {
                    "before": None,
                    "after": "arn:aws:lambda:us-east-1:1:function:hello:11",
                },
                "qualified_invoke_arn": {
                    "before": None,
                    "after": "arn:aws:apigateway:us-east-1:lambda:path/x",
                },
                "invoke_arn": {
                    "before": None,
                    "after": "arn:aws:apigateway:us-east-1:lambda:path/y",
                },
            },
        )
        self.assertEqual(list(filtered), ["layers"])

    def test_filter_drops_lambda_published_version(self):
        filtered = gi.filter_patchable_changes(
            {
                "source_code_hash": {
                    "before": "aaa",
                    "after": "bbb",
                },
                "version": {
                    "before": "14",
                    "after": "15",
                },
            },
        )
        self.assertEqual(list(filtered), ["source_code_hash"])

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

    def test_module_prefixed_address_patches_bare_resource_block(self):
        """module.X.type.name must target resource \"type\" \"name\" in the file."""
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "main.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_sns_topic" "events" {\n'
                    '  name             = "events"\n'
                    '  kms_master_key_id = "alias/aws/sns"\n'
                    "}\n"
                )
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf,
                    "module.test_support.aws_sns_topic.events",
                    {
                        "kms_master_key_id": {
                            "before": "alias/aws/sns",
                            "after": "",
                        },
                    },
                    value_key="after",
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertNotIn("kms_master_key_id", out)
            self.assertIn('name             = "events"', out)

    def test_module_prefixed_deleted_externally_removes_block(self):
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "main.tf")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(
                    'resource "aws_vpc_security_group_egress_rule" "s3_https" {\n'
                    '  from_port = 443\n'
                    '  to_port   = 443\n'
                    "}\n\n"
                    'resource "aws_security_group" "app" {\n'
                    '  name = "app"\n'
                    "}\n"
                )
            orig_hcledit = gi.is_hcledit_available
            gi.is_hcledit_available = lambda: False
            try:
                out = gi.apply_changes_to_file(
                    tf,
                    "module.network.aws_vpc_security_group_egress_rule.s3_https",
                    {},
                    deleted=True,
                )
            finally:
                gi.is_hcledit_available = orig_hcledit
            self.assertNotIn("s3_https", out)
            self.assertIn('resource "aws_security_group" "app"', out)

    def test_hcl_resource_type_name_strips_modules(self):
        self.assertEqual(
            gi._hcl_resource_type_name(
                "module.network.aws_vpc_security_group_egress_rule.s3_https"
            ),
            ("aws_vpc_security_group_egress_rule", "s3_https"),
        )
        self.assertEqual(
            gi._hcl_resource_type_name("aws_instance.foo[0]"),
            ("aws_instance", "foo"),
        )


if __name__ == "__main__":
    unittest.main()
