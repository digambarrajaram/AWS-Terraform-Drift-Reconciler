"""Overview cost aggregation helpers and /api/overview response shape.

Bugs covered:
- Prefer:count=exact returning 206 used to fail the whole Overview endpoint
- Double-encoded / string monthly_estimate_usd were skipped → $0 totals
- Rollback count used len(body) instead of Content-Range
- Current Drift uses last scan findings, not only status=open tickets

Run: python -m unittest tests.test_overview_cost
"""
import io
import json
import os
import unittest
from unittest.mock import MagicMock

from dashboard import serve
from dashboard.handler_base import _last_scan_drift_summary, _monthly_estimate_usd


class MonthlyEstimateTests(unittest.TestCase):
    def test_native_object(self):
        self.assertEqual(
            _monthly_estimate_usd({"monthly_estimate_usd": 7.59}),
            7.59,
        )

    def test_double_encoded_string(self):
        raw = json.dumps({"monthly_estimate_usd": 12.5})
        self.assertEqual(_monthly_estimate_usd(raw), 12.5)

    def test_numeric_string_inside_object(self):
        self.assertEqual(
            _monthly_estimate_usd({"monthly_estimate_usd": "3.25"}),
            3.25,
        )

    def test_null_and_empty(self):
        self.assertIsNone(_monthly_estimate_usd(None))
        self.assertIsNone(_monthly_estimate_usd({}))
        self.assertIsNone(_monthly_estimate_usd("not-json"))
        self.assertIsNone(_monthly_estimate_usd({"monthly_estimate_usd": True}))
        self.assertIsNone(_monthly_estimate_usd({"monthly_estimate_usd": -1}))


class LastScanDriftSummaryTests(unittest.TestCase):
    def test_aggregates_risk_levels(self):
        summary = _last_scan_drift_summary({
            "drift": {
                "found": True,
                "count": 2,
                "findings": [
                    {"resource_id": "a", "risk_level": "HIGH"},
                    {"resource_id": "b", "risk_level": "medium"},
                ],
            },
        })
        self.assertEqual(summary["count"], 2)
        self.assertTrue(summary["found"])
        self.assertFalse(summary["skipped"])
        self.assertEqual(
            summary["severity"],
            [{"severity": "HIGH", "count": 1}, {"severity": "MEDIUM", "count": 1}],
        )

    def test_skipped_plan(self):
        summary = _last_scan_drift_summary({
            "drift": {
                "found": False,
                "count": 0,
                "skipped": True,
                "reason": "Terraform plan failed",
                "findings": [],
            },
        })
        self.assertTrue(summary["skipped"])
        self.assertEqual(summary["reason"], "Terraform plan failed")
        self.assertEqual(summary["count"], 0)

    def test_missing_drift_block(self):
        summary = _last_scan_drift_summary({"mode": "trivy_only"})
        self.assertEqual(summary["count"], 0)
        self.assertFalse(summary["found"])
        self.assertEqual(summary["severity"], [])


class _Resp:
    def __init__(self, data, status_code=200, headers=None):
        self.data = data
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self.data


class OverviewEndpointTests(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        os.environ["SUPABASE_URL"] = "https://supabase.invalid"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "key"
        self._orig_get = serve.requests.get

    def tearDown(self):
        serve.requests.get = self._orig_get
        os.environ.clear()
        os.environ.update(self.old_env)

    def _call(self, responses, captured_urls=None):
        queue = list(responses)

        def fake_get(url, **_k):
            if captured_urls is not None:
                captured_urls.append(url)
            if not queue:
                raise AssertionError(f"unexpected GET {url}")
            return queue.pop(0)

        serve.requests.get = fake_get
        h = serve._Handler.__new__(serve._Handler)
        h.path = "/api/overview?scope=scope-a"
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._json_error = MagicMock()
        h._require_owned_scope = lambda _s: True
        h._serve_overview()
        if h._json_error.called:
            return {"_error": h._json_error.call_args[0]}
        raw = h.wfile.getvalue()
        return json.loads(raw.decode("utf-8") if raw else "{}")

    def test_accepts_206_and_sums_string_costs(self):
        urls = []
        payload = self._call([
            _Resp([{"severity": "HIGH", "count": 2}]),  # severity (open tickets)
            _Resp([], status_code=206, headers={"Content-Range": "0-0/3"}),  # rollback
            _Resp([{  # last scan — live drift even when open tickets exist
                "completed_at": "2026-09-01T00:00:00Z",
                "result_summary": {
                    "drift": {
                        "found": True,
                        "count": 1,
                        "findings": [{"resource_id": "aws_s3_bucket.x", "risk_level": "HIGH"}],
                    },
                },
            }]),
            _Resp([  # cost page
                {"cost_impact": {"monthly_estimate_usd": 10}},
                {"cost_impact": json.dumps({"monthly_estimate_usd": "2.5"})},
                {"cost_impact": None},
            ]),
        ], captured_urls=urls)
        self.assertNotIn("_error", payload)
        self.assertEqual(payload["rollback_count"], 3)
        self.assertEqual(payload["cost_impact"], 12.5)
        self.assertEqual(payload["cost_resource_count"], 2)
        self.assertEqual(payload["severity"][0]["severity"], "HIGH")
        self.assertEqual(payload["open_count"], 2)
        self.assertEqual(payload["last_scan_drift"]["count"], 1)
        self.assertEqual(
            payload["last_scan_drift"]["severity"],
            [{"severity": "HIGH", "count": 1}],
        )
        scan_url = next(u for u in urls if "scan_runs" in u)
        self.assertIn("result_summary", scan_url)
        # Open Rollbacks must match /api/rollback-data eligible filters.
        rollback_url = next(u for u in urls if "drift_events" in u and "pr_number" in u)
        self.assertIn("status=in.(open,resolved)", rollback_url)
        self.assertIn("changes_jsonb=not.is.null", rollback_url)
        self.assertIn("pr_number=not.is.null", rollback_url)
        self.assertNotIn("pr_type=eq.rollback", rollback_url)

    def test_current_drift_when_open_tickets_zero(self):
        """Scan History can show Drift:1 while status=open rows are empty."""
        payload = self._call([
            _Resp([]),  # no open tickets
            _Resp([], headers={"Content-Range": "*/0"}),
            _Resp([{
                "completed_at": "2026-09-30T12:00:00Z",
                "result_summary": {
                    "drift": {
                        "found": True,
                        "count": 1,
                        "findings": [{"resource_id": "aws_iam_role.x", "risk_level": "MEDIUM"}],
                    },
                },
            }]),
            _Resp([]),
        ])
        self.assertEqual(payload["open_count"], 0)
        self.assertEqual(payload["last_scan_drift"]["count"], 1)
        self.assertTrue(payload["last_scan_drift"]["found"])

    def test_no_cost_rows(self):
        payload = self._call([
            _Resp([]),
            _Resp([], headers={"Content-Range": "*/0"}),
            _Resp([]),
            _Resp([]),
        ])
        self.assertEqual(payload["cost_impact"], 0)
        self.assertEqual(payload["cost_resource_count"], 0)
        self.assertIsNone(payload["last_scan"])
        self.assertEqual(payload["last_scan_drift"]["count"], 0)
        self.assertEqual(payload["open_count"], 0)


if __name__ == "__main__":
    unittest.main()
