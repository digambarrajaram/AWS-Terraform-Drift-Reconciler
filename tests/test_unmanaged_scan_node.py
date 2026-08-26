"""Check unmanaged_scan_node subtracts real managed resources even in
unmanaged_only mode: state tracks aws_instance.WebServer, the live scan
sees WebServer + aws_instance.Rogue → only Rogue is flagged.  The old
code skipped load_managed_resources entirely in unmanaged_only mode,
flagging every live resource as unmanaged.

Run: python -m unittest tests.test_unmanaged_scan_node
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# agent.py uses plain top-level sibling imports — resolve them the same
# way the CLI does (python drift_reconciler/agent.py puts that dir first).
_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

import agent  # noqa: E402


class _FakeSupabase(BaseHTTPRequestHandler):
    """Environments lookup succeeds; every other table is empty."""

    def do_GET(self):
        body = b'[{"slug": "prod-esign"}]' if "/environments?" in self.path else b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _FakeSupabaseWithBucket(_FakeSupabase):
    """Same environments row, but the env now advertises a NEW state
    bucket — the vpc regression scenario: a stale .terraform/tfstate
    cached from an older bucket must not skip init."""

    def do_GET(self):
        body = (b'[{"slug": "prod-esign", "tf_state_bucket": "new-bucket",'
                b' "tf_lock_table": "tf-lock", "region": "us-east-1"}]'
                if "/environments?" in self.path else b"[]")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class _FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""


class UnmanagedScanNodeTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSupabase)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{self.server.server_address[1]}"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        self._orig = {
            "tf_dir": agent._tf_dir,
            "account": agent._account_label,
            "session": agent.get_aws_session,
            "scan": agent.unmanaged_scanner.scan_unmanaged_resources,
            "managed": agent.unmanaged_scanner.load_managed_resources,
            "stage": agent.report_stage,
            "run": agent.subprocess.run,
        }
        agent.subprocess.run = lambda *a, **k: _FakeResult()
        agent._tf_dir = "/tmp/fake-tf"
        agent._account_label = "prod-esign"
        agent.report_stage = lambda *a, **k: None
        agent.get_aws_session = lambda env: object()
        agent.unmanaged_scanner.scan_unmanaged_resources = lambda session, region: [
            {"type": "aws_instance", "id": "i-webserver", "raw_name": "WebServer",
             "arn": "arn:aws:ec2:us-east-1:1:instance/i-webserver"},
            {"type": "aws_instance", "id": "i-rogue", "raw_name": "Rogue",
             "arn": "arn:aws:ec2:us-east-1:1:instance/i-rogue"},
        ]
        agent.unmanaged_scanner.load_managed_resources = lambda tf_dir, env=None: [
            {"type": "aws_instance", "name": "WebServer",
             "arn": "arn:aws:ec2:us-east-1:1:instance/i-webserver"},
        ]

    def tearDown(self):
        agent._tf_dir = self._orig["tf_dir"]
        agent._account_label = self._orig["account"]
        agent.get_aws_session = self._orig["session"]
        agent.unmanaged_scanner.scan_unmanaged_resources = self._orig["scan"]
        agent.unmanaged_scanner.load_managed_resources = self._orig["managed"]
        agent.report_stage = self._orig["stage"]
        agent.subprocess.run = self._orig["run"]
        self.server.shutdown()
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_managed_resource_excluded_in_unmanaged_only_mode(self):
        result = agent.unmanaged_scan_node({"scan_mode": "unmanaged_only"})
        findings = result["drift_findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["resource_id"], "aws_instance.Rogue")
        self.assertTrue(result["drift_detected"])

    def test_stale_mismatched_backend_forces_reconfigure_before_subtraction(self):
        # vpc regression: unmanaged_scan_node never called
        # _ensure_terraform_init, so a stale .terraform/terraform.tfstate
        # cached from a DIFFERENT bucket passed the "already initialized"
        # skip and `terraform show` died with "Backend initialization
        # required" — every live resource got flagged unmanaged.  The env
        # row says new-bucket; the stale cache says old-bucket; init must
        # run with -reconfigure before state subtraction.
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSupabaseWithBucket)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        tf_dir = tempfile.mkdtemp(prefix="node_mismatch_")
        os.makedirs(os.path.join(tf_dir, ".terraform"))
        with open(os.path.join(tf_dir, ".terraform", "terraform.tfstate"), "w") as f:
            f.write(json.dumps({"version": 3, "backend": {
                "type": "s3", "config": {"bucket": "old-bucket"}}}))
        calls = []
        orig_run = agent.subprocess.run
        agent.subprocess.run = lambda *a, **k: calls.append((a, k)) or _FakeResult()
        try:
            os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
            agent._tf_dir = tf_dir
            result = agent.unmanaged_scan_node({"scan_mode": "unmanaged_only"})
        finally:
            agent.subprocess.run = orig_run
            server.shutdown()
        self.assertEqual(len(calls), 1, "mismatched cached backend must force init")
        self.assertIn("-reconfigure", calls[0][0][0])
        # Subtraction then ran against the re-initialized backend:
        # WebServer is tracked, only Rogue is flagged.
        self.assertEqual([f["resource_id"] for f in result["drift_findings"]],
                         ["aws_instance.Rogue"])


    def test_init_failure_aborts_scan_not_silent_empty_success(self):
        # Provider timeout / init failure must NOT look like "no unmanaged
        # resources found" — raise so the outer scan_runs finalizer marks
        # status=failed with the init error in result_summary.
        orig_ensure = agent._ensure_terraform_init
        writes: list[dict] = []

        def fake_update(run_id, **fields):
            writes.append({"run_id": run_id, **fields})

        agent._ensure_terraform_init = lambda *a, **k: (
            "Error: Failed to install provider registry.terraform.io/... "
            "timeout while downloading"
        )
        try:
            with self.assertRaises(RuntimeError) as ctx:
                agent.unmanaged_scan_node({"scan_mode": "unmanaged_only"})
            msg = str(ctx.exception)
            self.assertIn("terraform init failed", msg.lower())
            self.assertIn("timeout", msg.lower())

            # Mirror main()'s except path: mark scan_runs failed with
            # humanized result_summary carrying the init error.
            summary = agent.humanize_terraform_error(msg)
            fake_update(
                "run-init-fail",
                status="failed",
                result_summary=summary,
            )
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["status"], "failed")
            self.assertNotIn(writes[0]["status"], ("complete", "completed"))
            rs = writes[0]["result_summary"]
            self.assertIn("detail", rs)
            self.assertIn("terraform init failed", rs["detail"].lower())
            self.assertTrue(rs.get("summary"))
        finally:
            agent._ensure_terraform_init = orig_ensure


if __name__ == "__main__":
    unittest.main()
