"""
Serve the dashboard with Supabase credentials injected from the repo
.env file.  No hardcoded keys in HTML.

Usage:
    python dashboard/serve.py [--port 8080]
"""

import argparse
import http.server
import json
import os
import sys
from pathlib import Path

import requests
import subprocess

_REPO_ROOT = Path(__file__).resolve().parent.parent
import sys as _sys
_sys.path.insert(0, str(_REPO_ROOT))
from drift_reconciler.scan_runs import create_scan_run, update_scan_run
from drift_reconciler.rollback_runs import create_rollback_run
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
_VALID_SCOPES = {"scope-a", "scope-b"}


def _supabase_headers():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _supabase_get(path, params=None):
    url = f"{os.environ.get('SUPABASE_URL', '').rstrip('/')}/rest/v1/{path}"
    return requests.get(url, headers=_supabase_headers(), params=params, timeout=10)


def _tf_dir_for(scope: str) -> str:
    tf_dirs = {
        "scope-a": "terraform_code/ec2_terraform_account_a",
        "scope-b": "terraform_code/ec2_terraform_account_b",
    }
    return tf_dirs.get(scope, f"terraform_code/ec2_terraform_{scope}")


def _spawn_agent(scope: str, extra_args: list[str]) -> None:
    """Spawn agent.py as a non-blocking subprocess with the correct
    AWS profile and PYTHONPATH for *scope*."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # scope-a → account-a, scope-b → account-b
    aws_profile = "account-a" if scope == "scope-a" else "account-b"
    env["AWS_PROFILE"] = aws_profile
    cmd = [
        _sys.executable,
        str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
        "--tf-dir", _tf_dir_for(scope),
        "--account-label", scope,
    ] + extra_args
    subprocess.Popen(cmd, cwd=str(_REPO_ROOT), env=env)


def _load_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            if key.strip() not in os.environ:
                os.environ[key.strip()] = val.strip()


class _Handler(http.server.SimpleHTTPRequestHandler):
    _CACHEABLE = {".js", ".css", ".png", ".svg", ".woff2"}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/explorer", "/explorer.html", "/scan", "/scan.html", "/pr-queue", "/pr-queue.html", "/rollback", "/rollback.html", "/trends", "/trends.html"):
            self._serve_injected()
        elif path.endswith((".js", ".css", ".png")):
            self._serve_static(path)
        else:
            super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/scan":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_error(400, "Invalid or empty JSON body")
                return

            scope = body.get("scope", "")
            if scope not in _VALID_SCOPES:
                self._json_error(400, f"Invalid scope: {scope}. Must be scope-a or scope-b.")
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
            unmanaged = body.get("unmanaged_flag", False)
            try:
                run_id = create_scan_run(scope, unmanaged)
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            # Map scope to terraform directory (same as CI workflow).
            tf_dirs = {
                "scope-a": "terraform_code/ec2_terraform_account_a",
                "scope-b": "terraform_code/ec2_terraform_account_b",
            }
            tf_dir = tf_dirs.get(scope, f"terraform_code/ec2_terraform_{scope}")

            # Non-blocking subprocess — fire and respond 202 immediately.
            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--run-id", run_id,
            ]
            if unmanaged:
                cmd.append("--scan-unmanaged")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            # Keep .env AWS keys for Bedrock, but use scope-specific profile
            # for terraform AWS resource operations.
            env["AWS_PROFILE"] = "account-a" if scope == "scope-a" else "account-b"
            subprocess.Popen(cmd, cwd=str(_REPO_ROOT), env=env)

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

            if not pr_number or scope not in _VALID_SCOPES:
                self._json_error(400, "pr_number (integer) and scope (scope-a/scope-b) are required")
                return

            try:
                run_id = create_rollback_run(pr_number, scope, mode="preview")
            except Exception as se:
                self._json_error(502, f"Failed to create rollback run: {se}")
                return

            tf_dirs = {
                "scope-a": "terraform_code/ec2_terraform_account_a",
                "scope-b": "terraform_code/ec2_terraform_account_b",
            }
            tf_dir = tf_dirs.get(scope, f"terraform_code/ec2_terraform_{scope}")

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
            env["AWS_PROFILE"] = "account-a" if scope == "scope-a" else "account-b"
            subprocess.Popen(cmd, cwd=str(_REPO_ROOT), env=env)

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

            if not pr_number or scope not in _VALID_SCOPES:
                self._json_error(400, "pr_number (integer) and scope (scope-a/scope-b) are required")
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

            tf_dirs = {
                "scope-a": "terraform_code/ec2_terraform_account_a",
                "scope-b": "terraform_code/ec2_terraform_account_b",
            }
            tf_dir = tf_dirs.get(scope, f"terraform_code/ec2_terraform_{scope}")

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
            env["AWS_PROFILE"] = "account-a" if scope == "scope-a" else "account-b"
            subprocess.Popen(cmd, cwd=str(_REPO_ROOT), env=env)

            resp_body = json.dumps({"run_id": run_id}).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        else:
            self.send_error(404)

    def _json_error(self, status, message, **extra):
        payload = {"error": message}
        payload.update(extra)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Serve the drift dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        return 1

    # Clear stale running scans from previous crashed server sessions.
    try:
        r = requests.get(
            f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/scan_runs?select=id&status=eq.running",
            headers=_supabase_headers(), timeout=10)
        for row in (r.json() or []):
            requests.delete(
                f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/scan_runs?id=eq.{row['id']}",
                headers=_supabase_headers(), timeout=10)
        print(f"Cleared {len(r.json() or [])} stale running scan(s)")
    except Exception:
        pass

    print(f"Dashboard → http://localhost:{args.port}")
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), _Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
