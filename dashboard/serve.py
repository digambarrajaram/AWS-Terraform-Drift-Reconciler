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
import re
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

# ── Environment cache (30s TTL) ──────────────────────────────────────
_ENV_CACHE: dict = {}
_ENV_CACHE_TS = 0.0


def _get_active_environments() -> list[dict]:
    """Return all active environments from Supabase, cached for 30s."""
    global _ENV_CACHE, _ENV_CACHE_TS
    import time as _time
    now = _time.monotonic()
    if _ENV_CACHE and (now - _ENV_CACHE_TS) < 30:
        return list(_ENV_CACHE.values())  # list of row dicts

    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        try:
            resp = requests.get(
                f"{url}/rest/v1/environments?select=*&is_active=eq.true",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                _ENV_CACHE = {r["slug"]: r for r in resp.json()}
                _ENV_CACHE_TS = now
                return list(_ENV_CACHE.values())
        except requests.RequestException:
            if _ENV_CACHE:
                return list(_ENV_CACHE.values())  # serve stale cache
    # Fallback: serve stale cache (or empty if never populated)
    return list(_ENV_CACHE.values()) if _ENV_CACHE else []


def _get_valid_scopes() -> set[str]:
    return {e["slug"] for e in _get_active_environments()}


def _get_env_field(slug: str, field: str, default: str = "") -> str:
    """Return *field* from the environment row for *slug*, or *default*."""
    for e in _get_active_environments():
        if e["slug"] == slug:
            return e.get(field, default) or default
    return default


def _tf_dir_for(scope: str) -> str:
    return _get_env_field(scope, "tf_directory_path") or f"terraform_code/ec2_terraform_{scope}"


def _aws_profile_for(scope: str) -> str:
    return _get_env_field(scope, "aws_profile") or ("account-a" if scope == "scope-a" else "account-b")


def _configure_aws_env(env: dict, scope: str) -> None:
    """Set AWS_PROFILE in *env* only when the environment's auth_type
    is 'profile' or unset (transitional fallback).  For 'role'/'keys',
    the agent resolves credentials itself — a stale profile would break
    boto3 session creation."""
    auth_type = _get_env_field(scope, "auth_type") or ""
    if not auth_type or auth_type == "profile":
        env["AWS_PROFILE"] = _aws_profile_for(scope)


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
    _configure_aws_env(env, scope)
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
        if path in ("/", "/index.html", "/explorer", "/explorer.html", "/scan", "/scan.html", "/pr-queue", "/pr-queue.html", "/rollback", "/rollback.html", "/trends", "/trends.html", "/exceptions", "/exceptions.html", "/alerts", "/alerts.html", "/environments", "/environments.html"):
            self._serve_injected()
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/api/environments":
            self._serve_environments()
        elif path == "/api/notification-settings":
            self._serve_notification_settings()
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
            unmanaged = body.get("unmanaged_flag", False)
            try:
                run_id = create_scan_run(scope, unmanaged)
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            # Non-blocking subprocess — fire and respond 202 immediately.
            tf_dir = _tf_dir_for(scope)
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
            _configure_aws_env(env, scope)
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
            subprocess.Popen(cmd, cwd=str(_REPO_ROOT), env=env)

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
        else:
            self.send_error(404)

    def do_PATCH(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_patch(env_id)
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_delete(env_id)
        else:
            self.send_error(404)

    def _env_table(self):
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        return f"{url}/rest/v1/environments", headers

    def _upsert_env_secret(self, env_id, updates):
        """PATCH or POST to environment_secrets for *env_id*.
        *updates* is a dict of column→value pairs (e.g. ``{"github_token": "..."}``)."""
        secrets_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        from datetime import datetime, timezone
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        # PATCH existing row.  With return=representation, PostgREST
        # returns [] when no rows match (HTTP 200) vs. [{...}] when a
        # row was updated (HTTP 200).  Both are HTTP 200 — the body
        # distinguishes them.
        resp = requests.patch(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, json=payload, timeout=10)
        patched_rows = resp.json() if resp.text and resp.status_code == 200 else None
        if not patched_rows:
            # No row yet — INSERT, then PATCH to set the values.
            post_resp = requests.post(secrets_url, headers=headers, json={"environment_id": env_id}, timeout=10)
            if post_resp.status_code in (200, 201):
                requests.patch(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, json=payload, timeout=10)
            else:
                # INSERT failed — try PATCH with the full payload just in case.
                payload["environment_id"] = env_id
                requests.post(secrets_url, headers=headers, json=payload, timeout=10)

    def _serve_environments(self):
        table_url, headers = self._env_table()
        try:
            resp = requests.get(
                f"{table_url}?select=*&order=created_at",
                headers={k: v for k, v in headers.items() if k != "Prefer"},
                timeout=10,
            )
            if resp.status_code == 200:
                envs = resp.json() if resp.text else []

                # Fetch secrets to add masked token field.
                secrets_lookup = {}
                if envs:
                    ids = ",".join(e["id"] for e in envs)
                    s_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
                    s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                    s_headers = {"apikey": s_key, "Authorization": f"Bearer {s_key}"}
                    try:
                        s_resp = requests.get(
                            f"{s_url}?select=environment_id,github_token,aws_access_key_id,aws_secret_access_key&environment_id=in.({ids})",
                            headers=s_headers, timeout=10,
                        )
                        if s_resp.status_code == 200:
                            for row in (s_resp.json() or []):
                                secrets_lookup[row["environment_id"]] = row
                    except requests.RequestException:
                        pass

                def _mask(val):
                    if not val: return None
                    s = str(val)
                    if len(s) <= 4: return "••••"
                    return "••••" + s[-4:]

                for e in envs:
                    sec = secrets_lookup.get(e["id"], {})
                    tok = sec.get("github_token", "") if isinstance(sec, dict) else ""
                    access_key = sec.get("aws_access_key_id", "") if isinstance(sec, dict) else ""
                    secret_key = sec.get("aws_secret_access_key", "") if isinstance(sec, dict) else ""
                    e["github_token_configured"] = bool(tok)
                    e["github_token_masked"] = _mask(tok)
                    e["aws_access_key_configured"] = bool(access_key)
                    e["aws_access_key_masked"] = _mask(access_key)
                    e["aws_secret_key_configured"] = bool(secret_key)
                    e["aws_secret_key_masked"] = _mask(secret_key)

                data = json.dumps(envs).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(502, f"Supabase query failed ({resp.status_code})")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        slug = (body.get("slug") or "").strip()
        if not slug or not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
            self._json_error(400, "slug is required and must be URL-safe (lowercase alphanumeric and hyphens only).")
            return

        required = ["name", "aws_account_id", "region", "tf_state_bucket", "tf_directory_path"]
        row = {"slug": slug}
        for field in required:
            val = (body.get(field) or "").strip()
            if not val:
                self._json_error(400, f"{field} is required.")
                return
            row[field] = val

        # Optional fields
        for opt in ["aws_profile", "tf_lock_table", "scan_role_variable", "apply_role_secret_name", "apply_environment_name", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "aws_external_id"]:
            if body.get(opt):
                row[opt] = body[opt].strip()

        # Guard: auth_type='keys' requires keys.
        if row.get("auth_type") == "keys":
            keys_in_request = (body.get("_aws_access_key_id") or "").strip() and (body.get("_aws_secret_access_key") or "").strip()
            if not keys_in_request:
                self._json_error(400, "auth_type='keys' requires both aws_access_key_id and aws_secret_access_key.")
                return

        table_url, headers = self._env_table()
        try:
            resp = requests.post(table_url, headers=headers, json=row, timeout=10)
            if resp.status_code in (200, 201):
                created = resp.json()
                new_row = created[0] if isinstance(created, list) else created
                env_id = new_row.get("id")
                # Write secrets to environment_secrets if provided.
                secrets_to_write = {}
                for k in ("_github_token", "_aws_access_key_id", "_aws_secret_access_key"):
                    val = (body.get(k) or "").strip()
                    if val:
                        secrets_to_write[k.lstrip("_")] = val
                if secrets_to_write and env_id:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception:
                        pass  # non-fatal
                data = json.dumps(new_row).encode("utf-8")
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif resp.status_code == 409:
                # Slug exists — try reactivating a soft-deleted row.
                reactivate = requests.patch(
                    f"{table_url}?slug=eq.{slug}&is_active=eq.false",
                    headers=headers,
                    json={"is_active": True, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},
                    timeout=10,
                )
                if reactivate.status_code in (200, 204):
                    self.send_response(200)
                    data = json.dumps({"slug": slug, "reactivated": True}).encode("utf-8")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json_error(409, f"slug '{slug}' already exists.")
            else:
                self._json_error(502, f"Supabase insert failed ({resp.status_code}): {resp.text[:200]}")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_patch(self, env_id):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        allowed = {"name", "aws_account_id", "aws_profile", "region", "tf_state_bucket", "tf_lock_table", "tf_directory_path", "scan_role_variable", "apply_role_secret_name", "apply_environment_name", "is_active", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "aws_external_id"}
        updates = {}
        github_token_val = None
        aws_access_key_val = None
        aws_secret_key_val = None
        for k, v in body.items():
            if k == "_github_token":
                github_token_val = (str(v).strip() or None)
            elif k == "_aws_access_key_id":
                aws_access_key_val = (str(v).strip() or None)
            elif k == "_aws_secret_access_key":
                aws_secret_key_val = (str(v).strip() or None)
            elif k in allowed:
                updates[k] = v
        if not updates and not github_token_val and not aws_access_key_val and not aws_secret_key_val:
            self._json_error(400, "No valid fields to update.")
            return

        # Guard: switching to auth_type='keys' requires keys (either in this
        # request or already stored).
        if updates.get("auth_type") == "keys":
            have_new_keys = aws_access_key_val and aws_secret_key_val
            if not have_new_keys:
                # Check if keys already exist in environment_secrets.
                have_existing = False
                try:
                    s_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
                    s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                    s_resp = requests.get(
                        f"{s_url}?select=aws_access_key_id,aws_secret_access_key&environment_id=eq.{env_id}",
                        headers={"apikey": s_key, "Authorization": f"Bearer {s_key}"},
                        timeout=10,
                    )
                    if s_resp.status_code == 200 and s_resp.json():
                        row = s_resp.json()[0]
                        have_existing = bool((row.get("aws_access_key_id") or "").strip()) and bool((row.get("aws_secret_access_key") or "").strip())
                except Exception:
                    pass
                if not have_existing:
                    self._json_error(400, "auth_type='keys' requires both aws_access_key_id and aws_secret_access_key.")
                    return

        from datetime import datetime, timezone
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        table_url, headers = self._env_table()
        try:
            resp = requests.patch(f"{table_url}?id=eq.{env_id}", headers=headers, json=updates, timeout=10)
            if resp.status_code in (200, 204):
                secrets_to_write = {}
                for k, var in [("github_token", github_token_val), ("aws_access_key_id", aws_access_key_val), ("aws_secret_access_key", aws_secret_key_val)]:
                    if var:
                        secrets_to_write[k] = var
                if secrets_to_write:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception:
                        pass
                if resp.status_code == 200 and resp.text:
                    data = json.dumps(resp.json()).encode("utf-8")
                else:
                    data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "Environment not found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_delete(self, env_id):
        table_url, headers = self._env_table()
        from datetime import datetime, timezone
        try:
            resp = requests.patch(
                f"{table_url}?id=eq.{env_id}",
                headers=headers,
                json={"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()},
                timeout=10,
            )
            if resp.status_code in (200, 204):
                data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "Environment not found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_routing_rules_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        severity = body.get("severity", "").upper()
        if severity not in ("HIGH", "MEDIUM", "LOW"):
            self._json_error(400, "severity must be HIGH, MEDIUM, or LOW.")
            return

        channel = body.get("channel", "").lower()
        if channel not in ("pagerduty", "slack"):
            self._json_error(400, "channel must be pagerduty or slack.")
            return

        scope = body.get("scope") or None

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        table_url = f"{url}/rest/v1/severity_routing_rules"

        # Build match filter.
        filters = f"severity=eq.{severity}"
        if scope:
            filters += f"&scope=eq.{scope}"
        else:
            filters += "&scope=is.null"

        from datetime import datetime, timezone
        payload = {"severity": severity, "channel": channel, "scope": scope, "updated_at": datetime.now(timezone.utc).isoformat()}

        try:
            # Try PATCH existing row first.
            resp = requests.patch(f"{table_url}?{filters}", headers=headers, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                pass  # updated
            elif resp.status_code == 200 and resp.json():
                pass  # updated with representation
            else:
                # No existing row — INSERT.
                resp = requests.post(table_url, headers=headers, json=payload, timeout=10)
                if resp.status_code not in (200, 201):
                    self._json_error(502, f"Supabase upsert failed ({resp.status_code}): {resp.text[:200]}")
                    return
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")
            return

        data = json.dumps({"success": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_notification_test(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        channel = body.get("channel", "")
        if channel not in ("pagerduty", "slack"):
            self._json_error(400, "channel must be 'pagerduty' or 'slack'.")
            return

        scope = body.get("scope") or None

        def _fail(msg):
            data = json.dumps({"success": False, "error": msg}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        if channel == "pagerduty":
            try:
                from drift_reconciler.pagerduty_alert import trigger_pagerduty_alert
                kwargs = {
                    "summary": "Test alert from Drift Reconciler dashboard — please ignore",
                    "severity": "error",
                    "source": "Terraform Drift Engine",
                }
                if scope:
                    kwargs["account_label"] = scope
                result = trigger_pagerduty_alert(**kwargs)
                if not result:
                    _fail("PagerDuty returned empty response — check routing key.")
                    return
            except Exception as e:
                _fail(f"PagerDuty send failed: {e}")
                return
        else:
            try:
                from drift_reconciler.slack_notify import notify_all
                dummy = [{
                    "resource_id": "test.dashboard",
                    "risk_level": "LOW",
                    "drift_summary": "Test alert from Drift Reconciler dashboard — please ignore",
                }]
                acct = scope or "test"
                sent = notify_all(dummy, acct)
                if sent == 0:
                    _fail("Slack returned 0 sent — check webhook URL.")
                    return
            except Exception as e:
                _fail(f"Slack send failed: {e}")
                return

        data = json.dumps({"success": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_notification_settings_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        field = body.get("field", "")
        if field not in ("pagerduty_routing_key", "slack_webhook_url"):
            self._json_error(400, "field must be pagerduty_routing_key or slack_webhook_url.")
            return

        value = body.get("value")
        if not value or not str(value).strip():
            self._json_error(400, "value is required and must be non-empty.")
            return

        try:
            from drift_reconciler.notification_config import update_notification_secret
            ok = update_notification_secret(field, str(value).strip())
        except Exception as e:
            self._json_error(502, f"Failed to update: {e}")
            return

        if not ok:
            self._json_error(502, "Failed to update — Supabase may be unreachable.")
            return

        payload = {"success": True, f"{field}_configured": True}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_error(self, status, message, **extra):
        payload = {"error": message}
        payload.update(extra)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_notification_settings(self):
        try:
            from drift_reconciler.notification_config import get_notification_secrets
            secrets = get_notification_secrets()
        except Exception:
            secrets = {}

        def _mask(val):
            if not val:
                return None
            s = str(val)
            if len(s) <= 4:
                return "••••"
            return "••••" + s[-4:]

        pd_key = secrets.get("pagerduty_routing_key")
        slack_url = secrets.get("slack_webhook_url")

        payload = {
            "pagerduty_configured": bool(pd_key),
            "pagerduty_masked": _mask(pd_key),
            "slack_configured": bool(slack_url),
            "slack_masked": _mask(slack_url),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_api_exceptions(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        scope_raw = params.get("scope", [None])[0]
        if not scope_raw or scope_raw not in _get_valid_scopes():
            self._json_error(400, "Invalid or missing scope. Must be one of: " + ", ".join(sorted(_get_valid_scopes())) + ".")
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
        if scope not in _get_valid_scopes():
            self._json_error(400, f"Invalid scope: {scope}. Must be one of: " + ", ".join(sorted(_get_valid_scopes())) + ".")
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
