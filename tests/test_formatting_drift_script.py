"""formatting_drift_json must run as a subprocess (terraform_ops) and detect drift."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from drift_reconciler.formatting_drift_json import report_drift


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


if __name__ == "__main__":
    unittest.main()
