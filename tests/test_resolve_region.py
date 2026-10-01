"""drift_history.resolve_region — prefer environments.region over AWS_REGION/unknown.

Run: python -m unittest tests.test_resolve_region
"""
import os
import unittest
from unittest.mock import patch

from drift_reconciler.drift_history import resolve_region
from drift_reconciler.scope_resolution import ScopeConfigError


class ResolveRegionTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("AWS_REGION")
        os.environ.pop("AWS_REGION", None)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AWS_REGION", None)
        else:
            os.environ["AWS_REGION"] = self._prev

    @patch("drift_reconciler.scope_resolution.fetch_environment_row")
    def test_uses_environment_region(self, fetch):
        fetch.return_value = {"region": "us-east-1", "slug": "scope-a"}
        os.environ["AWS_REGION"] = "eu-west-1"
        self.assertEqual(resolve_region("scope-a"), "us-east-1")
        fetch.assert_called_once_with("scope-a")

    @patch("drift_reconciler.scope_resolution.fetch_environment_row")
    def test_skips_literal_unknown_from_env_row(self, fetch):
        fetch.return_value = {"region": "unknown"}
        os.environ["AWS_REGION"] = "ap-south-1"
        self.assertEqual(resolve_region("scope-a"), "ap-south-1")

    @patch("drift_reconciler.scope_resolution.fetch_environment_row")
    def test_falls_back_to_aws_region(self, fetch):
        fetch.side_effect = ScopeConfigError("missing")
        os.environ["AWS_REGION"] = "us-west-2"
        self.assertEqual(resolve_region("missing-scope"), "us-west-2")

    @patch("drift_reconciler.scope_resolution.fetch_environment_row")
    def test_never_persists_unknown_default(self, fetch):
        fetch.side_effect = ScopeConfigError("missing")
        self.assertEqual(resolve_region(""), "us-east-1")
        self.assertNotEqual(resolve_region(""), "unknown")


if __name__ == "__main__":
    unittest.main()
