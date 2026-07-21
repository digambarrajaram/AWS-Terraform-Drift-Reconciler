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
from urllib.parse import urlparse, parse_qs

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


def _validate_exception_entry_local(exception_type: str, entry: dict) -> tuple[bool, str | None]:
    """Same contract as ``validate_exception_entry`` in github_integration,
    replicated here so serve.py can validate without importing PyGithub."""
    from datetime import datetime

    if exception_type == "drift":
        addr = (entry.get("resource_address") or "").strip()
        if not addr:
            return False, "resource_address is required and must be a non-empty string."
        reason = (entry.get("reason") or "").strip()
        if not reason:
            return False, "reason is required and must be a non-empty string."
        expires = (entry.get("expires") or "").strip()
        if expires:
            try:
                exp_date = datetime.strptime(expires, "%Y-%m-%d").date()
                if exp_date <= datetime.now().date():
                    return False, f"expires ({expires}) is in the past."
            except ValueError:
                return False, f"expires ({expires}) is not a valid ISO date (YYYY-MM-DD)."
        return True, None

    if exception_type == "unmanaged":
        rt = (entry.get("resource_type") or "").strip()
        if not rt:
            return False, "resource_type is required and must be a non-empty string."
        pattern = (entry.get("resource_id_pattern") or "").strip()
        if not pattern:
            return False, "resource_id_pattern is required and must be a non-empty string."
        reason = (entry.get("reason") or "").strip()
        if not reason:
            return False, "reason is required and must be a non-empty string."
        return True, None

    return False, f"Unknown exception_type: {exception_type}"


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
        if path in ("/", "/index.html", "/explorer", "/explorer.html", "/scan", "/scan.html", "/pr-queue", "/pr-queue.html", "/rollback", "/rollback.html", "/trends", "/trends.html", "/exceptions", "/exceptions.html"):
            self._serve_injected()
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path.startswith("/api/exceptions"):
            self._serve_api_exceptions()
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
        elif path == "/api/exceptions":
            self._handle_api_exceptions_post()
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

    def _serve_api_exceptions(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        scope_raw = params.get("scope", [None])[0]
        if not scope_raw or scope_raw not in _VALID_SCOPES:
            self._json_error(400, "Invalid or missing scope. Must be scope-a or scope-b.")
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        base = f"{url}/rest/v1/drift_exception_registry"

        def _fetch(exception_type):
            try:
                resp = requests.get(
                    f"{base}?select=*&scope=eq.{scope_raw}&exception_type=eq.{exception_type}&active=eq.true&order=created_at.desc",
                    headers=headers, timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json() if resp.text else []
                return []
            except requests.RequestException:
                return []

        payload = {
            "drift_exceptions": _fetch("drift"),
            "unmanaged_exceptions": _fetch("unmanaged"),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_exceptions_post(self):
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

        exception_type = body.get("exception_type", "")
        if exception_type not in ("drift", "unmanaged"):
            self._json_error(400, "exception_type must be 'drift' or 'unmanaged'.")
            return

        action = body.get("action", "")
        if action not in ("add", "expire", "delete"):
            self._json_error(400, "action must be 'add', 'expire', or 'delete'.")
            return

        entry = body.get("entry")
        if not isinstance(entry, dict):
            self._json_error(400, "entry must be a JSON object.")
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        table_url = f"{url}/rest/v1/drift_exception_registry"

        if action == "add":
            ok, err = _validate_exception_entry_local(exception_type, entry)
            if not ok:
                self._json_error(400, err)
                return

            row = {"scope": scope, "exception_type": exception_type, "reason": entry.get("reason", "").strip()}
            if exception_type == "drift":
                row["resource_address"] = (entry.get("resource_address") or "").strip()
                row["drift_type"] = (entry.get("drift_type") or "*").strip()
                row["auto"] = bool(entry.get("auto"))
                expires = (entry.get("expires") or "").strip()
                if expires:
                    row["expires"] = expires
            else:
                row["resource_type"] = (entry.get("resource_type") or "").strip()
                row["resource_id_pattern"] = (entry.get("resource_id_pattern") or "").strip()
                cost = entry.get("max_monthly_cost_usd")
                if cost is not None and cost != "":
                    row["max_monthly_cost_usd"] = float(cost)
            if entry.get("approved_by"):
                row["approved_by"] = entry["approved_by"].strip()

            try:
                resp = requests.post(table_url, headers=headers, json=row, timeout=10)
                if resp.status_code in (200, 201):
                    created = resp.json()
                    row_id = created[0]["id"] if isinstance(created, list) else created["id"]
                    data = json.dumps({"id": row_id}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json_error(502, f"Supabase insert failed ({resp.status_code}): {resp.text[:200]}")
            except requests.RequestException as e:
                self._json_error(502, f"Supabase unreachable: {e}")

        elif action == "expire":
            self._do_exception_update(scope, exception_type, entry, headers, table_url, {"expires": (entry.get("expires") or "").strip()})

        elif action == "delete":
            self._do_exception_update(scope, exception_type, entry, headers, table_url, {"active": False})

    def _do_exception_update(self, scope, exception_type, entry, headers, table_url, updates):
        filter_parts = [f"scope=eq.{scope}", f"exception_type=eq.{exception_type}", "active=eq.true"]
        if exception_type == "drift":
            addr = (entry.get("resource_address") or "").strip()
            if not addr:
                self._json_error(400, "resource_address is required.")
                return
            filter_parts.append(f"resource_address=eq.{addr}")
        else:
            rt = (entry.get("resource_type") or "").strip()
            pat = (entry.get("resource_id_pattern") or "").strip()
            if not rt or not pat:
                self._json_error(400, "resource_type and resource_id_pattern are required.")
                return
            filter_parts.append(f"resource_type=eq.{rt}")
            filter_parts.append(f"resource_id_pattern=eq.{pat}")

        filter_str = "&".join(filter_parts)
        try:
            resp = requests.patch(f"{table_url}?{filter_str}", headers=headers, json=updates, timeout=10)
            if resp.status_code in (200, 204):
                data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "No matching active exception entry found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

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
        elif "exceptions" in path:
            fname = "exceptions.html"
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
