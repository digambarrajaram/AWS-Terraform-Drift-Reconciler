"""Drift report formatting: in-process path, fallbacks, and agent error gate."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from drift_reconciler.formatting_drift_json import (
    _lookup_file_path,
    report_drift,
)
from drift_reconciler.agent import _drift_pipeline_failed
from drift_reconciler.drift_findings import agent_node, build_drift_findings


class FormattingDriftScriptTests(unittest.TestCase):
    def test_script_subprocess_imports_without_repo_root_on_path(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(root, "drift_reconciler", "formatting_drift_json.py")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, script],
            cwd=tempfile.gettempdir(),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("Usage:", result.stdout + result.stderr)

    def test_empty_resource_drift_falls_back_to_resource_changes(self):
        plan = {
            "resource_drift": [],
            "prior_state": {
                "values": {
                    "root_module": {
                        "resources": [
                            {"address": "aws_instance.foo"},
                        ],
                    },
                },
            },
            "resource_changes": [
                {
                    "address": "aws_instance.foo",
                    "change": {
                        "actions": ["update"],
                        "before": {"instance_type": "t3.micro"},
                        "after": {"instance_type": "t3.small"},
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = report_drift(plan, tf_dir=tmp, scope=None)
        self.assertEqual(report["report_type"], "drift")
        self.assertEqual(len(report["resources"]), 1)
        self.assertEqual(report["resources"][0]["address"], "aws_instance.foo")

    def test_noisy_resource_drift_still_uses_resource_changes_update(self):
        """Non-empty resource_drift with only tags_all must not hide real updates."""
        plan = {
            "resource_drift": [
                {
                    "address": "aws_instance.foo",
                    "change": {
                        "actions": ["update"],
                        "before": {"tags_all": {"a": "1"}},
                        "after": {"tags_all": {"a": "1", "b": "2"}},
                    },
                },
            ],
            "prior_state": {
                "values": {
                    "root_module": {
                        "resources": [{"address": "aws_instance.foo"}],
                    },
                },
            },
            "resource_changes": [
                {
                    "address": "aws_instance.foo",
                    "change": {
                        "actions": ["update"],
                        "before": {
                            "instance_type": "t3.micro",
                            "tags_all": {"a": "1"},
                        },
                        "after": {
                            "instance_type": "t3.small",
                            "tags_all": {"a": "1", "b": "2"},
                        },
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = report_drift(plan, tf_dir=tmp, scope=None)
        self.assertEqual(report["report_type"], "drift")
        self.assertIn("instance_type", report["resources"][0]["changes"])

    def test_replace_is_not_classified_as_deleted_externally(self):
        plan = {
            "resource_drift": [],
            "prior_state": {
                "values": {
                    "root_module": {
                        "resources": [{"address": "aws_instance.foo"}],
                    },
                },
            },
            "resource_changes": [
                {
                    "address": "aws_instance.foo",
                    "change": {
                        "actions": ["delete", "create"],
                        "before": {"ami": "ami-old"},
                        "after": {"ami": "ami-new"},
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = report_drift(plan, tf_dir=tmp, scope=None)
        self.assertEqual(report["report_type"], "drift")
        self.assertNotEqual(report["resources"][0].get("status"), "deleted_externally")
        self.assertIn("ami", report["resources"][0]["changes"])

    def test_update_with_empty_visible_diff_still_reported(self):
        plan = {
            "resource_drift": [],
            "prior_state": {
                "values": {
                    "root_module": {
                        "resources": [{"address": "aws_instance.foo"}],
                    },
                },
            },
            "resource_changes": [
                {
                    "address": "aws_instance.foo",
                    "mode": "managed",
                    "change": {
                        "actions": ["update"],
                        "before": {"tags_all": {"a": "1"}},
                        "after": {"tags_all": {"a": "2"}},
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = report_drift(plan, tf_dir=tmp, scope=None)
        # tags_all is read-only and filtered; update must still surface.
        self.assertEqual(report["report_type"], "drift")
        self.assertIn("_plan_change", report["resources"][0]["changes"])


class FilePathLookupTests(unittest.TestCase):
    def test_module_and_index_addresses_resolve(self):
        index = {"aws_instance.foo": "/tmp/main.tf"}
        self.assertEqual(
            _lookup_file_path(index, "module.vpc.aws_instance.foo[0]"),
            "/tmp/main.tf",
        )


class AgentNodeStateReportTests(unittest.TestCase):
    def test_agent_node_uses_state_drift_report_not_messages(self):
        report = {
            "report_type": "drift",
            "resources": [{
                "address": "aws_instance.foo",
                "changes": {"instance_type": {"before": "t3.micro", "after": "t3.small"}},
                "security_impact": "low",
            }],
        }

        class _BoomLLM:
            def invoke(self, _messages):
                raise RuntimeError("llm down")

        import drift_reconciler.drift_findings as df
        orig = df._get_llm
        df._get_llm = lambda: _BoomLLM()
        try:
            out = agent_node({
                "messages": [{"role": "user", "content": "no json here"}],
                "drift_report": report,
                "drift_findings": [],
                "run_id": None,
            })
        finally:
            df._get_llm = orig

        self.assertTrue(out["drift_detected"])
        self.assertEqual(len(out["drift_findings"]), 1)
        self.assertEqual(out["drift_findings"][0]["resource_id"], "aws_instance.foo")

    def test_build_findings_from_opaque_plan_change(self):
        report = {
            "report_type": "drift",
            "resources": [{
                "address": "aws_instance.foo",
                "changes": {
                    "_plan_change": {
                        "before": ["update"],
                        "after": "sensitive_or_unknown_attribute_change",
                    }
                },
                "security_impact": "low",
            }],
        }
        findings = build_drift_findings(report)
        self.assertEqual(len(findings), 1)


class DriftPipelineFailedTests(unittest.TestCase):
    def test_valid_drift_json_with_error_substring_is_not_failure(self):
        report = json.dumps({
            "report_type": "drift",
            "resources": [{
                "address": "aws_instance.foo",
                "changes": {
                    "user_data": {
                        "before": "echo Error: failed",
                        "after": "echo ok",
                    },
                },
            }],
        })
        self.assertFalse(_drift_pipeline_failed(report))

    def test_pipeline_error_string_is_failure(self):
        self.assertTrue(
            _drift_pipeline_failed("Formatting Drift JSON Failed:\nboom")
        )

    def test_no_drift_report_is_success(self):
        self.assertFalse(
            _drift_pipeline_failed(json.dumps({"report_type": "no_drift", "resources": []}))
        )


if __name__ == "__main__":
    unittest.main()
