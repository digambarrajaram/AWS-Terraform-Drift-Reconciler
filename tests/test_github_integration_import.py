"""Regression: github_integration must import with only the repo root on PYTHONPATH."""
import os
import subprocess
import sys
import unittest


class GithubIntegrationImportTests(unittest.TestCase):
    def test_github_integration_imports_from_repo_root_only(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = root
        result = subprocess.run(
            [sys.executable, "-c", "import drift_reconciler.github_integration"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stderr or result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
