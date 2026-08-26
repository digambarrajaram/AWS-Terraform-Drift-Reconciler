"""Check _ensure_terraform_init's init-detection keys off
.terraform/terraform.tfstate, not modules.json (which module-less configs
like prod-kyc/prod-cra's ec2_terraform_account_a/ never get written):
an already-initialized module-less dir must skip init entirely; a
genuinely uninitialized dir must run it; a cached backend bucket that
differs from backend_config must force -reconfigure.

Run: python -m unittest tests.test_ensure_terraform_init
"""
import json
import os
import sys
import tempfile
import unittest

# agent.py uses plain top-level sibling imports — resolve them the same
# way the CLI does (python drift_reconciler/agent.py puts that dir first).
_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402


def _tfstate(bucket: str) -> str:
    return json.dumps({"version": 3, "backend": {
        "type": "s3", "config": {"bucket": bucket},
    }})


class _FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""


class EnsureTerraformInitTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig_run = agent.subprocess.run
        agent.subprocess.run = lambda *a, **k: self.calls.append((a, k)) or _FakeResult()
        self.tf_dir = tempfile.mkdtemp(prefix="ensure_init_")

    def tearDown(self):
        agent.subprocess.run = self._orig_run

    def test_moduleless_initialized_skips_init(self):
        # .terraform/terraform.tfstate present, NO .terraform/modules/
        # modules.json — the prod-kyc/prod-cra layout after a real init.
        os.makedirs(os.path.join(self.tf_dir, ".terraform"))
        with open(os.path.join(self.tf_dir, ".terraform", "terraform.tfstate"), "w") as f:
            f.write(_tfstate("sec-acc-tf-state-285629514281"))
        self.assertEqual(agent._ensure_terraform_init(self.tf_dir), "")
        self.assertEqual(self.calls, [])  # init never ran

    def test_uninitialized_runs_init(self):
        self.assertEqual(agent._ensure_terraform_init(self.tf_dir), "")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0][0], ["terraform", "init", "-no-color", "-input=false"])

    def test_backend_bucket_mismatch_forces_reconfigure(self):
        os.makedirs(os.path.join(self.tf_dir, ".terraform"))
        with open(os.path.join(self.tf_dir, ".terraform", "terraform.tfstate"), "w") as f:
            f.write(_tfstate("old-bucket"))
        self.assertEqual(
            agent._ensure_terraform_init(self.tf_dir, backend_config={"bucket": "new-bucket"}),
            "",
        )
        self.assertEqual(len(self.calls), 1)
        self.assertIn("-reconfigure", self.calls[0][0][0])

    def test_matching_backend_config_skips_init(self):
        os.makedirs(os.path.join(self.tf_dir, ".terraform"))
        with open(os.path.join(self.tf_dir, ".terraform", "terraform.tfstate"), "w") as f:
            f.write(_tfstate("same-bucket"))
        self.assertEqual(
            agent._ensure_terraform_init(self.tf_dir, backend_config={"bucket": "same-bucket"}),
            "",
        )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
