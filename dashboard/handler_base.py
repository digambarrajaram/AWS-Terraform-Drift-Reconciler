"""HTTP handler base: auth, routing, static file helpers."""
from __future__ import annotations

import hmac
import http.server
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

from drift_reconciler.scan_runs import create_scan_run, update_scan_run
from drift_reconciler.rollback_runs import create_rollback_run

from dashboard.env import (
    _configure_aws_env,
    _get_active_environments,
    _get_valid_scopes,
    _tf_dir_for,
)
from dashboard.paths import _REPO_ROOT, _DASHBOARD_DIR
from dashboard.process_runner import (
    _RUNNING,
    _RUNNING_LOCK,
    _spawn_with_capture,
)

class HandlerBase(http.server.SimpleHTTPRequestHandler):
    _CACHEABLE = {".js", ".css", ".png", ".svg", ".woff2"}

    def _check_auth(self) -> bool:
        """Return True if the request is authorised.

        When ``API_ACCESS_TOKEN`` is not configured in the environment
        every request passes (a warning is logged once at startup).
        When it *is* configured the ``X-Api-Access-Token`` request
        header must match, using a constant-time comparison."""
        token = os.environ.get("API_ACCESS_TOKEN", "").strip()
        if not token:
            return True  # auth disabled — startup warning already printed
        request_token = (self.headers.get("X-Api-Access-Token") or "").strip()
        if not request_token:
            return False
        return hmac.compare_digest(token, request_token)

    def _unauthorized(self) -> None:
        body = json.dumps(
            {"error": "Missing or invalid X-Api-Access-Token header"}
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        # Auth-gate API data endpoints — everything under /api/ returns
        # infrastructure details, masked secrets, or exception entries.
        if path.startswith("/api/") and not self._check_auth():
            self._unauthorized()
            return

        if path in ("/", "/index.html", "/explorer", "/explorer.html", "/scan", "/scan.html", "/pr-queue", "/pr-queue.html", "/rollback", "/rollback.html", "/trends", "/trends.html", "/exceptions", "/exceptions.html", "/alerts", "/alerts.html", "/environments", "/environments.html"):
            self._serve_injected()
        elif path == "/favicon.ico":
            self.send_response(204)
        elif path == "/api/config":
            self._serve_config()
            self.end_headers()
        elif path == "/api/environments":
            self._serve_environments()
        elif path == "/api/pending-applies":
            self._serve_pending_applies()
        elif path.startswith("/api/pending-applies/") and path.endswith("/pr-details"):
            self._serve_pr_details()
        elif path.startswith("/api/pending-applies/") and path.endswith("/logs"):
            # Must be before the bare /{id} handler — otherwise logs requests
            # are served as a pending_applies row (status=applied, no lines)
            # and the drawer spins on "Waiting for output…" forever.
            self._serve_run_logs()
        elif path.startswith("/api/pending-applies/"):
            self._serve_pending_apply_single()
        elif path == "/api/notification-settings":
            self._serve_notification_settings()
        elif path == "/api/github-settings":
            self._serve_github_settings()
        elif path.startswith("/api/exceptions"):
            self._serve_api_exceptions()
        elif path.startswith("/api/scan/") and path.endswith("/logs"):
            self._serve_run_logs()
        elif path.endswith((".js", ".css", ".png")):
            self._serve_static(path)
        else:
            super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]

        # GitHub webhook — authenticated by X-Hub-Signature-256, not the
        # dashboard API token.  Must run BEFORE the _check_auth() gate.
        if path == "/api/webhooks/github":
            self._handle_github_webhook()
            return

        if not self._check_auth():
            self._unauthorized()
            return

        if path.startswith("/api/scan/") and path.endswith("/cancel"):
            self._cancel_run(path, "scan_runs")
        elif path.startswith("/api/pending-applies/") and path.endswith("/decision"):
            self._handle_pending_apply_decision(path)
        elif path.startswith("/api/pending-applies/") and path.endswith("/cancel"):
            self._cancel_run(path, "pending_applies")
        elif path.startswith("/api/rollback/") and path.endswith("/cancel"):
            self._cancel_run(path, "rollback_runs")
        elif path == "/api/scan/trivy-only":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_error(400, "Invalid or empty JSON body")
                return

            scope = body.get("scope", "")
            if scope not in _get_valid_scopes():
                self._json_error(400, f"Invalid scope: {scope}. Must be one of: " + ", ".join(sorted(_get_valid_scopes())) + ".")
                return

            # Check for an existing running scan in this scope.
            try:
                resp = _supabase_get(
                    "scan_runs",
                    {"select": "id", "scope": f"eq.{scope}", "status": "eq.running", "limit": "1"}
                )
                if resp.status_code == 200 and resp.json():
                    existing_id = resp.json()[0]["id"]
                    self._json_error(409, f"Scan already running for {scope}", run_id=existing_id)
                    return
            except requests.RequestException as exc:
                self._json_error(502, f"Supabase unreachable: {exc}")
                return

            try:
                run_id = create_scan_run(scope, unmanaged_flag=False, scan_type="trivy_only")
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            with _RUNNING_LOCK:
                for rid, (p, _e, _sc) in list(_RUNNING.items()):
                    if p.poll() is None and _sc == scope:
                        self._json_error(409, f"Scan already running for {scope} (run: {rid})")
                        return

            tf_dir = _tf_dir_for(scope)

            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--run-id", run_id,
                "--trivy-only",
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)

            resp_body = json.dumps({"run_id": run_id}).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        elif path == "/api/scan":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_error(400, "Invalid or empty JSON body")
                return

            scope = body.get("scope", "")
            if scope not in _get_valid_scopes():
                self._json_error(400, f"Invalid scope: {scope}. Must be one of: " + ", ".join(sorted(_get_valid_scopes())) + ".")
                return

            # Check for an existing running scan in this scope.
            try:
                resp = _supabase_get(
                    "scan_runs",
                    {"select": "id", "scope": f"eq.{scope}", "status": "eq.running", "limit": "1"}
                )
                if resp.status_code == 200 and resp.json():
                    existing_id = resp.json()[0]["id"]
                    self._json_error(409, f"Scan already running for {scope}", run_id=existing_id)
                    return
            except requests.RequestException as exc:
                self._json_error(502, f"Supabase unreachable: {exc}")
                return

            # Insert the scan_run row.
            scan_mode = body.get("scan_mode", "drift_only")
            if scan_mode not in ("drift_only", "drift_and_unmanaged", "unmanaged_only"):
                self._json_error(400, "scan_mode must be drift_only, drift_and_unmanaged, or unmanaged_only.")
                return
            unmanaged_flag_for_db = scan_mode in ("drift_and_unmanaged", "unmanaged_only")
            try:
                run_id = create_scan_run(scope, unmanaged_flag_for_db, scan_type=scan_mode)
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            # Guard against overlapping scans for the same scope — two
            # terraform processes racing for the same state lock will
            # both fail, and the second lock-acquisition error is confusing.
            with _RUNNING_LOCK:
                for rid, (p, _e, _sc) in list(_RUNNING.items()):
                    if p.poll() is None and _sc == scope:
                        self._json_error(409, f"Scan already running for {scope} (run: {rid})")
                        return

            tf_dir = _tf_dir_for(scope)

            # Non-blocking subprocess — fire and respond 202 immediately.
            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--run-id", run_id,
                "--scan-mode", scan_mode,
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)

            resp_body = json.dumps({"run_id": run_id}).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        elif path == "/api/rollback/preview":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_error(400, "Invalid or empty JSON body")
                return

            pr_number = body.get("pr_number")
            scope = body.get("scope", "")

            if not pr_number or scope not in _get_valid_scopes():
                self._json_error(400, "pr_number (integer) and a valid scope are required")
                return

            try:
                run_id = create_rollback_run(pr_number, scope, mode="preview")
            except Exception as se:
                self._json_error(502, f"Failed to create rollback run: {se}")
                return

            tf_dir = _tf_dir_for(scope)
            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--rollback-preview",
                "--rollback-pr", str(pr_number),
                "--run-id", run_id,
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)

            resp_body = json.dumps({"run_id": run_id}).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        elif path == "/api/rollback/execute":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_error(400, "Invalid or empty JSON body")
                return

            pr_number = body.get("pr_number")
            scope = body.get("scope", "")

            if not pr_number or scope not in _get_valid_scopes():
                self._json_error(400, "pr_number (integer) and a valid scope are required")
                return

            # Concurrency check — only one rollback for a given PR at a time.
            try:
                resp = _supabase_get(
                    "rollback_runs",
                    {"select": "id", "pr_number": f"eq.{pr_number}", "status": "eq.running", "limit": "1"}
                )
                if resp.status_code == 200 and resp.json():
                    existing_id = resp.json()[0]["id"]
                    self._json_error(409, f"Rollback already running for PR #{pr_number}", run_id=existing_id)
                    return
            except requests.RequestException as exc:
                self._json_error(502, f"Supabase unreachable: {exc}")
                return

            try:
                run_id = create_rollback_run(pr_number, scope, mode="execute")
            except Exception as se:
                self._json_error(502, f"Failed to create rollback run: {se}")
                return

            tf_dir = _tf_dir_for(scope)
            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--rollback",
                "--rollback-pr", str(pr_number),
                "--run-id", run_id,
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)

            resp_body = json.dumps({"run_id": run_id}).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        elif path == "/api/exceptions":
            self._handle_api_exceptions_post()
        elif path == "/api/routing-rules":
            self._handle_routing_rules_post()
        elif path == "/api/notification-settings/test":
            self._handle_notification_test()
        elif path == "/api/environments":
            self._handle_environments_post()
        elif path == "/api/notification-settings":
            self._handle_notification_settings_post()
        elif path == "/api/github-settings":
            self._handle_github_settings_post()
        else:
            self.send_error(404)

    def do_PATCH(self):
        path = self.path.split("?")[0]

        if not self._check_auth():
            self._unauthorized()
            return

        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_patch(env_id)
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]

        if not self._check_auth():
            self._unauthorized()
            return

        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_delete(env_id)
        else:
            self.send_error(404)

    def end_headers(self):
        ext = os.path.splitext(self.path.split("?")[0])[1]
        if ext in self._CACHEABLE:
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def translate_path(self, path):
        """Serve all files from the dashboard directory."""
        rel = path.lstrip("/") or "index.html"
        return str(_DASHBOARD_DIR / rel)

    def _serve_static(self, path):
        fpath = _DASHBOARD_DIR / path.lstrip("/")
        if not fpath.is_file():
            self.send_error(404)
            return
        data = fpath.read_bytes()
        ext = os.path.splitext(path)[1]
        ctype = {".js": "application/javascript", ".css": "text/css", ".png": "image/png"}.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Cancel in-progress run ─────────────────────────────────────
