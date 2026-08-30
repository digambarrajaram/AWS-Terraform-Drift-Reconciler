"""HTTP handler mixin: ConfigMixin."""
from __future__ import annotations

import json
import os

from dashboard.paths import _DASHBOARD_DIR

class ConfigMixin:
    def _serve_config(self):
        """GET /api/config — Supabase connection details for the frontend's
        direct supabase-js queries (PrQueue/Explorer/Rollback read
        drift_events straight from Supabase; Approvals alone proxies through
        serve.py, which is why it kept working).  Mirrors the Express
        api-server's route; serve.py must serve it in production, where that
        server isn't in the deploy."""
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        if not url or not anon:
            self._json_error(503, "Backend not configured — set SUPABASE_URL and SUPABASE_ANON_KEY")
            return
        payload = {"supabaseUrl": url, "supabaseAnonKey": anon}
        repo = os.environ.get("GITHUB_REPO", "").strip()
        if repo:
            payload["githubRepo"] = repo
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_injected(self):
        try:
            self._serve_injected_impl()
        except Exception as e:
            print(f"[dashboard] ERROR serving injected page: {e}")
            self.send_error(500)

    def _serve_injected_impl(self):
        path = self.path.split("?")[0]
        if "pr-queue" in path:
            fname = "pr-queue.html"
        elif "rollback" in path and "api" not in path:
            fname = "rollback.html"
        elif "explorer" in path:
            fname = "explorer.html"
        elif "scan" in path:
            fname = "scan.html"
        elif "trends" in path:
            fname = "trends.html"
        elif "exceptions" in path:
            fname = "exceptions.html"
        elif "alerts" in path:
            fname = "alerts.html"
        elif "environments" in path:
            fname = "environments.html"
        else:
            fname = "index.html"
        html = (_DASHBOARD_DIR / fname).read_text(encoding="utf-8")
        html = html.replace("__SUPABASE_URL__", os.environ.get("SUPABASE_URL", ""))
        anon = os.environ.get("SUPABASE_ANON_KEY", "")
        if not anon:
            raise RuntimeError("SUPABASE_ANON_KEY is not set in .env")
        html = html.replace("__SUPABASE_ANON_KEY__", anon)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[dashboard] {args[0]}")


