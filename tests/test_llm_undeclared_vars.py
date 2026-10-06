"""LLM security fixes must not introduce undeclared var.X references.

Run: python -m unittest tests.test_llm_undeclared_vars
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import trivy_agent as ta  # noqa: E402


class UndeclaredVarRejectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="undeclared_var_")
        with open(os.path.join(self.tmp, "main.tf"), "w", encoding="utf-8") as f:
            f.write(
                'resource "aws_cloudwatch_log_group" "lambda" {\n'
                '  name = "/aws/lambda/hello"\n'
                "}\n"
            )
        # Module has no variable blocks at all.

    def test_reject_new_undeclared_var_refs(self):
        issue = {
            "rule_id": "AWS-0017",
            "title": "Enable CMK encryption of CloudWatch Log Groups",
            "resource": "aws_cloudwatch_log_group.lambda",
            "resource_type": "aws_cloudwatch_log_group",
            "start_line": 1,
            "end_line": 3,
            "resolution": "Enable CMK encryption",
        }
        bad_block = (
            'resource "aws_cloudwatch_log_group" "lambda" {\n'
            '  name              = "/aws/lambda/${var.name_prefix}-hello"\n'
            '  kms_key_id        = "arn:aws:kms:us-east-1:123:key/abc"\n'
            '  tags              = merge(var.tags, { Name = "${var.name_prefix}-lambda-logs" })\n'
            "}\n"
        )
        with patch.object(ta, "_try_cheap_regex_fix", return_value=None), \
             patch.object(ta, "_llm_fix_block", return_value=bad_block), \
             patch.object(ta, "_valid_attributes_for", return_value=None):
            result = ta._apply_fix(os.path.join(self.tmp, "main.tf"), issue)
        self.assertIsNone(result)
        # Original file unchanged
        with open(os.path.join(self.tmp, "main.tf"), encoding="utf-8") as f:
            self.assertNotIn("var.name_prefix", f.read())

    def test_allow_when_variable_is_declared(self):
        with open(os.path.join(self.tmp, "variables.tf"), "w", encoding="utf-8") as f:
            f.write('variable "name_prefix" {}\nvariable "tags" { default = {} }\n')
        issue = {
            "rule_id": "AWS-0017",
            "title": "Enable CMK",
            "resource": "aws_cloudwatch_log_group.lambda",
            "resource_type": "aws_cloudwatch_log_group",
            "start_line": 1,
            "end_line": 3,
            "resolution": "Enable CMK encryption",
        }
        good_block = (
            'resource "aws_cloudwatch_log_group" "lambda" {\n'
            '  name       = "/aws/lambda/${var.name_prefix}-hello"\n'
            '  kms_key_id = "arn:aws:kms:us-east-1:123:key/abc"\n'
            '  tags       = var.tags\n'
            "}\n"
        )
        with patch.object(ta, "_try_cheap_regex_fix", return_value=None), \
             patch.object(ta, "_llm_fix_block", return_value=good_block), \
             patch.object(ta, "_valid_attributes_for", return_value=None):
            result = ta._apply_fix(os.path.join(self.tmp, "main.tf"), issue)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
