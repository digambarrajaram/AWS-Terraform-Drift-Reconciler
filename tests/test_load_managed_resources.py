"""Check load_managed_resources's init handling:
- a fully initialized dir WITHOUT .terraform/modules/modules.json must
  still run terraform show and return state (prod-kyc layout: module-less
  config — init never writes modules.json; the old modules.json guard
  returned [] here and flagged every live resource as unmanaged)
- an uninitialized dir fail-softs to [] via terraform show's exit code

Run: python -m unittest tests.test_load_managed_resources
"""
import json
import unittest

from drift_reconciler import unmanaged_scanner as us


class _FakeResult:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class LoadManagedResourcesTests(unittest.TestCase):
    def setUp(self):
        self._orig_run = us.subprocess.run
        self.calls = []

    def tearDown(self):
        us.subprocess.run = self._orig_run

    def test_state_read_without_modules_json(self):
        state = json.dumps({"values": {"root_module": {"resources": [
            {"type": "aws_instance", "name": "WebServer",
             "values": {"arn": "arn:aws:ec2:1:instance/i-1"}},
        ]}}})
        us.subprocess.run = lambda *a, **k: self.calls.append(a) or _FakeResult(0, state)
        # tf_dir with NO .terraform/modules/modules.json (never initialized
        # as far as the old guard was concerned) — show must still run.
        resources = us.load_managed_resources("/tmp/never-inited-here")
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["type"], "aws_instance")
        self.assertEqual(resources[0]["name"], "WebServer")

    def test_show_failure_fail_softs_to_empty(self):
        us.subprocess.run = lambda *a, **k: self.calls.append(a) or \
            _FakeResult(1, "", "no backend configured — run terraform init")
        self.assertEqual(us.load_managed_resources("/tmp/uninitialized"), [])


if __name__ == "__main__":
    unittest.main()
