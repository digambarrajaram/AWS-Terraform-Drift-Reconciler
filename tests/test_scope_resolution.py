"""Regression tests for scope → terraform directory / IAM role resolution."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import dashboard.env as dashboard_env
from drift_reconciler.scope_resolution import (
    ScopeConfigError,
    detect_workload,
    fetch_environment_row,
    infer_workload,
    preflight_scope_configurations,
    resolve_apply_role_arn,
    resolve_scope_binding,
    validate_environment_scope_config,
    validate_role_arn_for_scope,
)


class DetectWorkloadTests(unittest.TestCase):
    def test_lambda_slug_with_ec2_path_is_ambiguous(self):
        with self.assertRaises(ScopeConfigError):
            detect_workload("lambda", "terraform_code/ec2_terraform_account_a")

    def test_ec2_from_path_only(self):
        self.assertEqual(
            detect_workload("scope-a", "terraform_code/ec2_terraform_account_a"),
            "ec2",
        )


class RoleValidationTests(unittest.TestCase):
    def test_lambda_scope_rejects_ec2_role(self):
        with self.assertRaises(ScopeConfigError) as ctx:
            validate_role_arn_for_scope(
                "arn:aws:iam::123456789012:role/drift-reconciler-apply-EC2",
                "lambda",
                "lambda",
            )
        self.assertIn("ec2", str(ctx.exception).lower())

    def test_lambda_scope_accepts_lambda_role(self):
        validate_role_arn_for_scope(
            "arn:aws:iam::123456789012:role/drift-reconciler-apply-lambda",
            "lambda",
            "lambda",
        )

    def test_resolve_apply_role_arn_rewrites_ec2_suffix_for_lambda(self):
        corrected = resolve_apply_role_arn(
            "arn:aws:iam::285629514281:role/drift-reconciler-apply-EC2",
            "lambda",
            "lambda",
        )
        self.assertEqual(
            corrected,
            "arn:aws:iam::285629514281:role/drift-reconciler-apply-LAMBDA",
        )


class InferWorkloadTests(unittest.TestCase):
    def test_lambda_from_tf_dir_when_slug_is_neutral(self):
        tmp = tempfile.mkdtemp(prefix="scope_lambda_tf_")
        with open(os.path.join(tmp, "lambda.tf"), "w", encoding="utf-8") as fh:
            fh.write('resource "aws_lambda_function" "hello" {}\n')
        self.assertEqual(
            infer_workload("account-a", "terraform/account-a", tmp),
            "lambda",
        )


class TfDirValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scope_tf_")

    def test_lambda_dir_must_declare_lambda_resource(self):
        with open(os.path.join(self.tmp, "main.tf"), "w", encoding="utf-8") as fh:
            fh.write('resource "aws_instance" "x" {}\n')
        with self.assertRaises(ScopeConfigError):
            validate_environment_scope_config(
                {
                    "slug": "lambda",
                    "tf_directory_path": "terraform/lambda_stack",
                    "aws_role_arn": "arn:aws:iam::1:role/drift-reconciler-apply-lambda",
                },
                self.tmp,
            )

    def test_ec2_seed_dir_under_repo(self):
        tf_dir = os.path.join(_ROOT, "terraform_code", "ec2_terraform_account_a")
        env = {
            "slug": "scope-a",
            "tf_directory_path": "terraform_code/ec2_terraform_account_a",
            "aws_role_arn": "arn:aws:iam::605134452604:role/drift-reconciler-apply-EC2",
        }
        validate_environment_scope_config(env, tf_dir)


class FetchEnvironmentRowTests(unittest.TestCase):
    def test_missing_scope_raises_explicit_error(self):
        with patch("drift_reconciler.scope_resolution.requests.get") as mock_get:
            mock_get.return_value = type(
                "R", (), {"status_code": 200, "text": "[]", "json": lambda self: []}
            )()
            with patch.dict(
                os.environ,
                {
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SERVICE_ROLE_KEY": "key",
                },
                clear=False,
            ):
                with self.assertRaises(ScopeConfigError) as ctx:
                    fetch_environment_row("missing-scope")
        self.assertIn("missing-scope", str(ctx.exception))
        self.assertIn("ec2_terraform", str(ctx.exception))


class TfDirForDashboardTests(unittest.TestCase):
    def test_no_silent_ec2_legacy_fallback(self):
        with patch(
            "drift_reconciler.scope_resolution.fetch_environment_row",
            side_effect=ScopeConfigError("no row"),
        ):
            with self.assertRaises(RuntimeError):
                dashboard_env._tf_dir_for("lambda", user_id="user-1")


class PreflightDuplicateTests(unittest.TestCase):
    def test_duplicate_tf_dir_across_scopes_fails(self):
        tf_dir = os.path.join(_ROOT, "terraform_code", "ec2_terraform_account_a")
        envs = [
            {
                "slug": "scope-a",
                "tf_directory_path": "terraform_code/ec2_terraform_account_a",
                "aws_role_arn": "arn:aws:iam::1:role/drift-reconciler-apply-EC2",
            },
            {
                "slug": "scope-b",
                "tf_directory_path": "terraform_code/ec2_terraform_account_a",
                "aws_role_arn": "arn:aws:iam::1:role/drift-reconciler-apply-EC2-b",
            },
        ]
        with self.assertRaises(ScopeConfigError):
            preflight_scope_configurations(envs, resolve_dirs=False)

    def test_seed_scopes_resolve_distinct_bindings(self):
        base = os.path.join(_ROOT, "terraform_code")
        envs = [
            {
                "slug": "scope-a",
                "tf_directory_path": "ec2_terraform_account_a",
                "aws_role_arn": "arn:aws:iam::605134452604:role/drift-reconciler-apply-EC2",
            },
            {
                "slug": "scope-b",
                "tf_directory_path": "ec2_terraform_account_b",
                "aws_role_arn": "arn:aws:iam::605134452604:role/drift-reconciler-apply-EC2-b",
            },
        ]
        bindings = []
        for e in envs:
            tf_dir = os.path.join(base, os.path.basename(e["tf_directory_path"]))
            bindings.append(resolve_scope_binding(e, tf_dir))
        self.assertNotEqual(bindings[0].tf_dir, bindings[1].tf_dir)
        self.assertNotEqual(bindings[0].role_arn, bindings[1].role_arn)


if __name__ == "__main__":
    unittest.main()
