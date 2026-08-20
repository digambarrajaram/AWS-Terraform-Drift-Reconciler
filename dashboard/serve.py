"""
Serve the dashboard with Supabase credentials injected from the repo
.env file.  No hardcoded keys in HTML.

Usage:
    python dashboard/serve.py [--port 8080]
"""

import argparse
import hashlib
import hmac
import http.server
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
import sys as _sys
_sys.path.insert(0, str(_REPO_ROOT))
from drift_reconciler.scan_runs import create_scan_run, update_scan_run
from drift_reconciler.rollback_runs import create_rollback_run
from drift_reconciler.environment_credentials import resolve_tf_dir
from drift_reconciler.utils import mask_secret as _mask
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"

# ── Live log capture ─────────────────────────────────────────────────
_LOG_DIR = Path("/tmp/drift-logs")
_LOG_BUFFERS: dict[str, deque] = {}
_LOG_LOCK = threading.Lock()
_LOG_MAXLINES = 2000

# Running subprocesses keyed by run_id so the cancel endpoint can
# terminate them gracefully (SIGTERM → wait → SIGKILL).
# Value is (Popen, env, scope) — env is needed by _force_unlock_tf,
# scope is used by the overlapping-run guard.
_RUNNING: dict[str, tuple[subprocess.Popen, dict, str]] = {}
_RUNNING_LOCK = threading.Lock()
# Run IDs that were cancelled — the exit-watcher checks this to decide
# whether to attempt a terraform force-unlock.
_CANCELLED: set[str] = set()

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
    env = next((e for e in _get_active_environments() if e["slug"] == scope), None)
    if env is None:
        return f"terraform_code/ec2_terraform_{scope}"  # legacy fallback, unchanged
    try:
        return resolve_tf_dir(env)
    except RuntimeError as exc:
        # Surface git-clone failures clearly instead of letting a bad path
        # silently reach the subprocess and fail later with a confusing
        # "directory not found" error.
        print(f"  ⚠ resolve_tf_dir failed for scope={scope}: {exc}")
        raise


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

    elif exception_type == "security":
        addr = (entry.get("resource_address") or "").strip()
        if not addr:
            return False, "resource_address is required and must be a non-empty string."
        rule_id = (entry.get("rule_id") or "").strip()
        if not rule_id:
            return False, "rule_id is required and must be a non-empty string."
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

    elif exception_type == "unmanaged":
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


def _force_unlock_tf(run_id: str, scope: str, env: dict | None = None) -> None:
    """Release a stale terraform DynamoDB state lock by deleting the
    lock item directly, bypassing ``terraform force-unlock`` entirely.

    Uses the same AWS credentials the original scan had so auth matches
    exactly.  Results are logged into the run's log buffer."""
    lock_table = _get_env_field(scope, "tf_lock_table") or "terraform-locks"
    region = (env or os.environ).get("AWS_REGION", "us-east-1")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    def _log(msg: str) -> None:
        with _LOG_LOCK:
            b = _LOG_BUFFERS.get(run_id)
            if b is not None:
                b.append(f"[{ts}] [cancel] {msg}")

    _log(f"Checking DynamoDB lock table '{lock_table}' in {region}")

    try:
        import boto3
        import botocore.exceptions

        access_key = (env or os.environ).get("AWS_ACCESS_KEY_ID", "").strip()
        secret_key = (env or os.environ).get("AWS_SECRET_ACCESS_KEY", "").strip()

        if access_key and secret_key:
            ddb = boto3.client("dynamodb", region_name=region,
                               aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key)
        else:
            ddb = boto3.client("dynamodb", region_name=region)

        resp = ddb.scan(TableName=lock_table, Limit=5)
        items = resp.get("Items", [])

        if not items:
            _log(f"✓ No lock items in '{lock_table}' — nothing to release")
            return

        deleted = 0
        for item in items:
            lock_id = item.get("LockID", {}).get("S", "?")
            ddb.delete_item(TableName=lock_table, Key={"LockID": {"S": lock_id}})
            deleted += 1
            _log(f"✓ Deleted lock: {lock_id}")

        _log(f"✓ Released {deleted} lock(s) from '{lock_table}'")

    except botocore.exceptions.ClientError as exc:
        err = exc.response["Error"]["Code"]
        _log(f"✗ DynamoDB {err}: {exc.response['Error']['Message'][:200]}")
    except Exception as exc:
        _log(f"✗ force-unlock raised: {exc}")


def _spawn_with_capture(cmd: list[str], run_id: str, env: dict, cwd: str, scope: str = "") -> subprocess.Popen:
    """Spawn *cmd* as a fire-and-forget subprocess with stdout captured.

    Inserts ``-u`` after the interpreter so piped stdout is unbuffered
    (without it the terminal pane shows nothing until the process is
    nearly done).  Every line is timestamped and written to an in-memory
    ring buffer keyed by *run_id* and to ``/tmp/drift-logs/{run_id}.log``.
    """
    # ponytail: the ring buffer holds 2000 lines per run — fine for the
    # single-digit concurrent runs this server sees.  If we ever fan out
    # to dozens of parallel scans, add a TTL-based cleanup in a periodic
    # thread or a __del__ hook on the Popen wrapper.
    cmd = list(cmd)
    cmd.insert(1, "-u")  # force unbuffered stdout

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / f"{run_id}.log"

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    with _LOG_LOCK:
        _LOG_BUFFERS[run_id] = deque(maxlen=_LOG_MAXLINES)
    with _RUNNING_LOCK:
        _RUNNING[run_id] = (proc, env.copy() if env else os.environ.copy(), scope or "")

    def _stream() -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                for line in proc.stdout:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    entry = f"[{ts}] {line.rstrip()}"
                    with _LOG_LOCK:
                        buf = _LOG_BUFFERS.get(run_id)
                        if buf is not None:
                            buf.append(entry)
                    f.write(entry + "\n")
                    f.flush()
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    threading.Thread(target=_stream, daemon=True, name=f"log-{run_id[:8]}").start()

    def _watch_exit() -> None:
        proc.wait()
        was_cancelled = run_id in _CANCELLED
        if was_cancelled:
            _CANCELLED.discard(run_id)
        with _RUNNING_LOCK:
            _pair = _RUNNING.pop(run_id, None)
        _run_scope = _pair[2] if _pair else ""

        if proc.returncode == 0:
            return

        # If the process was cancelled, terraform might have left a
        # stale DynamoDB lock — release it before the next scan starts.
        # The cancel handler already wrote 'cancelled' to the DB, so
        # skip the failed-status patch below entirely.
        if was_cancelled:
            _force_unlock_tf(run_id, _run_scope, env)
            return

        url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url_base or not key:
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        with _LOG_LOCK:
            buf = _LOG_BUFFERS.get(run_id)
            tail = list(buf)[-15:] if buf else []
        error_summary = "\n".join(tail)[:2000]

        # pending_applies is here too: apply/revert jobs use the row id as
        # their run_id, and a hard-killed job (agent.py exception paths all
        # _finish(), but e.g. SIGKILL doesn't) must still become 'failed' so
        # the log poller stops.
        for table, result_col in (
            ("scan_runs", "result_summary"),
            ("rollback_runs", "result"),
            ("pending_applies", "result"),
        ):
            try:
                resp = requests.get(
                    f"{url_base}/rest/v1/{table}?select=status&id=eq.{run_id}",
                    headers=headers, timeout=5,
                )
                if resp.status_code != 200 or not resp.json():
                    continue
                row = resp.json()[0]
                if row["status"] in (
                    "complete", "failed", "cancelled",
                    "applied", "reverted_gate_blocked", "manual_revert_required",
                ):
                    return
                requests.patch(
                    f"{url_base}/rest/v1/{table}?id=eq.{run_id}",
                    headers=headers,
                    json={
                        "status": "failed",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        result_col: {"error": f"Process exited with code {proc.returncode}", "log_tail": error_summary},
                    },
                    timeout=5,
                )
                return
            except requests.RequestException:
                continue

    threading.Thread(target=_watch_exit, daemon=True, name=f"watch-{run_id[:8]}").start()
    return proc


def _cleanup_old_logs() -> None:
    """Delete log files older than 24 h and evict ring buffers for terminal
    runs whose database rows are more than 24 h old.  Called once at startup."""
    import time as _time
    cutoff = _time.time() - 86400

    # ── File cleanup ──
    if _LOG_DIR.is_dir():
        deleted = 0
        for f in _LOG_DIR.glob("*.log"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
        if deleted:
            print(f"[startup] Removed {deleted} old log file(s) from {_LOG_DIR}")

    # ── Ring buffer eviction ──
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with _LOG_LOCK:
        run_ids = list(_LOG_BUFFERS.keys())
    if not run_ids:
        return

    evicted = 0
    for rid in run_ids:
        terminal = False
        try:
            for table in ("scan_runs", "rollback_runs"):
                resp = requests.get(
                    f"{url}/rest/v1/{table}?select=status,started_at&id=eq.{rid}",
                    headers=headers, timeout=5,
                )
                if resp.status_code == 200 and resp.json():
                    row = resp.json()[0]
                    status = row.get("status", "")
                    if status in ("complete", "failed"):
                        started = row.get("started_at") or ""
                        if started:
                            try:
                                from datetime import datetime as _dt
                                ts = _dt.fromisoformat(started.replace("Z", "+00:00"))
                                if _time.time() - ts.timestamp() > 86400:
                                    terminal = True
                            except (ValueError, TypeError):
                                pass
                    break
        except Exception:
            continue

        if terminal:
            with _LOG_LOCK:
                _LOG_BUFFERS.pop(rid, None)
            evicted += 1

    if evicted:
        print(f"[startup] Evicted {evicted} stale ring buffer(s)")


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

    # ── Auth ─────────────────────────────────────────────────────────
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
        elif path.startswith("/api/pending-applies/") and path.endswith("/logs"):
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

    def _env_table(self):
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        return f"{url}/rest/v1/environments", headers

    def _upsert_env_secret(self, env_id, updates):
        """PATCH or POST to environment_secrets for *env_id*.
        *updates* is a dict of column→value pairs (e.g. ``{"github_token": "..."}``).

        Raises RuntimeError on ANY failed write (non-200 PATCH, failed
        INSERT, or failed value-PATCH) — a silent half-write leaves a row
        with NULL keys, which later breaks auth_type='keys' with no trace."""
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
        if resp.status_code != 200:
            raise RuntimeError(f"environment_secrets PATCH failed ({resp.status_code}): {resp.text[:200]}")
        patched_rows = resp.json() if resp.text else None
        if not patched_rows:
            # No row yet — INSERT, then PATCH to set the values.
            post_resp = requests.post(secrets_url, headers=headers, json={"environment_id": env_id}, timeout=10)
            if post_resp.status_code not in (200, 201):
                raise RuntimeError(f"environment_secrets INSERT failed ({post_resp.status_code}): {post_resp.text[:200]}")
            patch_resp = requests.patch(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, json=payload, timeout=10)
            if patch_resp.status_code != 200:
                # Clean up the just-inserted empty row — leaving it behind
                # is exactly the NULL-keys state that breaks keys auth.
                try:
                    requests.delete(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, timeout=10)
                except requests.RequestException:
                    pass
                raise RuntimeError(
                    f"environment_secrets value PATCH after INSERT failed "
                    f"({patch_resp.status_code}): {patch_resp.text[:200]}"
                )

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
                            f"{s_url}?select=environment_id,github_token,aws_access_key_id,aws_secret_access_key,webhook_secret&environment_id=in.({ids})",
                            headers=s_headers, timeout=10,
                        )
                        if s_resp.status_code == 200:
                            for row in (s_resp.json() or []):
                                secrets_lookup[row["environment_id"]] = row
                    except requests.RequestException:
                        pass

                for e in envs:
                    sec = secrets_lookup.get(e["id"], {})
                    tok = sec.get("github_token", "") if isinstance(sec, dict) else ""
                    access_key = sec.get("aws_access_key_id", "") if isinstance(sec, dict) else ""
                    secret_key = sec.get("aws_secret_access_key", "") if isinstance(sec, dict) else ""
                    webhook_sec = sec.get("webhook_secret", "") if isinstance(sec, dict) else ""
                    e["github_token_configured"] = bool(tok)
                    e["github_token_masked"] = _mask(tok)
                    e["aws_access_key_configured"] = bool(access_key)
                    e["aws_access_key_masked"] = _mask(access_key)
                    e["aws_secret_key_configured"] = bool(secret_key)
                    e["aws_secret_key_masked"] = _mask(secret_key)
                    e["webhook_secret_configured"] = bool(webhook_sec)
                    e["webhook_secret_masked"] = _mask(webhook_sec)

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
        for opt in ["aws_profile", "tf_lock_table", "apply_environment_name", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "scan_role_arn", "aws_external_id"]:
            if body.get(opt):
                row[opt] = body[opt].strip()

        # Guard: auth_type='keys' requires keys.
        if row.get("auth_type") == "keys":
            keys_in_request = (body.get("_aws_access_key_id") or "").strip() and (body.get("_aws_secret_access_key") or "").strip()
            if not keys_in_request:
                self._json_error(400, "auth_type='keys' requires both aws_access_key_id and aws_secret_access_key.")
                return

        # Guard: auth_type is required for new environments and must be
        # 'role' or 'keys'.  Legacy values ('profile' / NULL) are only
        # permitted on UPDATE for existing environments (scope-a, scope-b).
        at = (row.get("auth_type") or "").strip()
        if at not in ("role", "keys"):
            self._json_error(400, "auth_type is required for new environments and must be 'role' or 'keys'.")
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
                for k in ("_github_token", "_aws_access_key_id", "_aws_secret_access_key", "_webhook_secret"):
                    val = (body.get(k) or "").strip()
                    if val:
                        secrets_to_write[k.lstrip("_")] = val
                if secrets_to_write and env_id:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception as exc:
                        import traceback
                        print(
                            f"  ✗ environment_secrets write FAILED for env_id={env_id} "
                            f"(slug={row.get('slug')}, keys={sorted(secrets_to_write)}) — "
                            f"the environment row exists but its secrets were NOT saved: {exc}",
                            file=sys.stderr,
                        )
                        traceback.print_exc()
                        self._json_error(502,
                            f"Environment created, but secret write failed for "
                            f"{', '.join(sorted(secrets_to_write))}: {exc}")
                        return
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

        allowed = {"name", "aws_account_id", "aws_profile", "region", "tf_state_bucket", "tf_lock_table", "tf_directory_path", "apply_environment_name", "is_active", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "scan_role_arn", "aws_external_id"}
        updates = {}
        github_token_val = None
        aws_access_key_val = None
        aws_secret_key_val = None
        webhook_secret_val = None
        for k, v in body.items():
            if k == "_github_token":
                github_token_val = (str(v).strip() or None)
            elif k == "_aws_access_key_id":
                aws_access_key_val = (str(v).strip() or None)
            elif k == "_aws_secret_access_key":
                aws_secret_key_val = (str(v).strip() or None)
            elif k == "_webhook_secret":
                webhook_secret_val = (str(v).strip() or None)
            elif k in allowed:
                updates[k] = v
        if not updates and not github_token_val and not aws_access_key_val and not aws_secret_key_val and not webhook_secret_val:
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
                for k, var in [("github_token", github_token_val), ("aws_access_key_id", aws_access_key_val), ("aws_secret_access_key", aws_secret_key_val), ("webhook_secret", webhook_secret_val)]:
                    if var:
                        secrets_to_write[k] = var
                if secrets_to_write:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception as exc:
                        import traceback
                        print(
                            f"  ✗ environment_secrets write FAILED for env_id={env_id} "
                            f"(keys={sorted(secrets_to_write)}) — environment updated, "
                            f"secrets NOT saved: {exc}",
                            file=sys.stderr,
                        )
                        traceback.print_exc()
                        self._json_error(502,
                            f"Environment updated, but secret write failed for "
                            f"{', '.join(sorted(secrets_to_write))}: {exc}")
                        return
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
            # Try PATCH existing row first.  With Prefer: return=representation,
            # Supabase returns 200 + [{...}] when a row was matched, or
            # 200 + [] when no rows matched — the body distinguishes them.
            resp = requests.patch(f"{table_url}?{filters}", headers=headers, json=payload, timeout=10)
            patched = resp.status_code in (200, 204) and resp.json() if resp.text else False
            if not patched:
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

    def _serve_github_settings(self):
        try:
            from drift_reconciler.github_settings import get_masked_github_token
            result = get_masked_github_token()
        except Exception:
            result = {"github_configured": False, "github_masked": None}

        data = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_github_settings_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        # Only one field exists on this singleton, so we accept
        # github_token directly rather than using the {field, value}
        # indirection the notification-settings endpoint needs (which
        # multiplexes pagerduty_routing_key / slack_webhook_url).
        token = (body.get("github_token") or "").strip()
        if not token:
            self._json_error(400, "github_token is required and must be non-empty.")
            return

        try:
            from drift_reconciler.github_settings import update_github_token
            ok = update_github_token(token)
        except Exception as e:
            self._json_error(502, f"Failed to update: {e}")
            return

        if not ok:
            self._json_error(
                502,
                "Failed to update — Supabase may be unreachable or the "
                "app_settings table has not been seeded yet (run "
                "migrations/create_app_settings_table.sql in the SQL Editor)."
            )
            return

        payload = {"success": True, "github_configured": True}
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
            "security_exceptions": _fetch("security"),
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
        if exception_type not in ("drift", "unmanaged", "security"):
            self._json_error(400, "exception_type must be 'drift', 'unmanaged', or 'security'.")
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
            elif exception_type == "security":
                row["resource_address"] = (entry.get("resource_address") or "").strip()
                row["rule_id"] = (entry.get("rule_id") or "").strip()
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
                # Normalize to lowercase — prevents "Digambar",
                # "digambar", and "Digambar R" from becoming 3
                # separate entries in the approved_by column.
                row["approved_by"] = entry["approved_by"].strip().lower()

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
        elif exception_type == "security":
            addr = (entry.get("resource_address") or "").strip()
            rule_id = (entry.get("rule_id") or "").strip()
            if not addr or not rule_id:
                self._json_error(400, "resource_address and rule_id are required.")
                return
            filter_parts.append(f"resource_address=eq.{addr}")
            filter_parts.append(f"rule_id=eq.{rule_id}")
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

    # ── Cancel in-progress run ─────────────────────────────────────
    def _cancel_run(self, path, table):
        """POST /api/scan/{run_id}/cancel or /api/rollback/{run_id}/cancel

        Marks the run row as 'cancelled' and terminates the subprocess
        gracefully (SIGTERM → 5 s wait → SIGKILL) so terraform can
        release its state lock."""
        run_id = path.split("/")[3]  # /api/scan/{run_id}/cancel

        # Write the cancelled status to Supabase FIRST so _watch_exit
        # sees it and doesn't overwrite it with 'failed'.
        url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if url_base and key:
            for attempt in range(2):
                try:
                    requests.patch(
                        f"{url_base}/rest/v1/{table}?id=eq.{run_id}",
                        headers={"apikey": key, "Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json",
                                 "Prefer": "return=minimal"},
                        json={"status": "cancelled",
                              "completed_at": datetime.now(timezone.utc).isoformat()},
                        timeout=5,
                    )
                    break
                except requests.RequestException:
                    if attempt == 1:
                        print(f"[cancel] Failed to write cancelled status for {run_id} — "
                              f"the exit watcher will mark it as failed.", file=sys.stderr)

        # Terminate the subprocess gracefully first so terraform can
        # release the DynamoDB state lock.  Only force-kill if it
        # doesn't exit within 5 seconds.
        with _RUNNING_LOCK:
            pair = _RUNNING.get(run_id)
            proc = pair[0] if pair else None
        if proc is not None and proc.poll() is None:
            _CANCELLED.add(run_id)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        if not url_base or not key:
            self._json_error(502, "Supabase not configured")
            return
        data = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Pending applies (dashboard approval gate) ────────────────────
    def _serve_pending_applies(self):
        """GET /api/pending-applies?status=awaiting_approval&scope=scope-a

        Lists pending_applies rows.  *status* defaults to
        ``awaiting_approval``; pass ``status=all`` for every row.
        Optional *scope* filter."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        status = params.get("status", ["awaiting_approval"])[0]
        scope = params.get("scope", [None])[0]

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}

        filters = []
        if status and status != "all":
            filters.append(f"status=eq.{status}")
        if scope:
            filters.append(f"scope=eq.{scope}")
        query = f"{url}/rest/v1/pending_applies"
        if filters:
            query += "?" + "&".join(filters)
        query += ("&" if filters else "?") + "order=created_at.desc"

        try:
            resp = requests.get(query, headers=headers, timeout=10)
            if resp.status_code != 200:
                self._json_error(502, f"Supabase query failed ({resp.status_code})")
                return
            rows = resp.json() if resp.text else []
        except requests.RequestException as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return

        data = json.dumps(rows).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_pending_apply_single(self):
        """GET /api/pending-applies/{id} — one row, for live status polling."""
        pending_id = self.path.split("/")[3]
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        try:
            resp = requests.get(
                f"{url}/rest/v1/pending_applies?select=*&id=eq.{pending_id}&limit=1",
                headers=headers, timeout=10,
            )
            rows = resp.json() if resp.text and resp.status_code == 200 else []
            if not rows:
                self._json_error(404, "Pending apply row not found.")
                return
            data = json.dumps(rows[0]).encode("utf-8")
        except requests.RequestException as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_pr_details(self):
        """GET /api/pending-applies/{id}/pr-details

        Fetches the GitHub PR (title, body, commits, files, checks,
        mergeable/conflict state) for the pending row's PR via the
        environment's repo + token — the same info a human reviewer
        sees on GitHub, so Approve/Reject can be decided in-dashboard."""
        pending_id = self.path.split("/")[3]

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        try:
            resp = requests.get(
                f"{url}/rest/v1/pending_applies"
                f"?select=pr_number,scope&id=eq.{pending_id}&limit=1",
                headers=headers, timeout=10,
            )
            rows = resp.json() if resp.text and resp.status_code == 200 else []
            if not rows:
                self._json_error(404, "Pending apply row not found.")
                return
            pr_number = rows[0].get("pr_number")
            scope = rows[0].get("scope")
        except requests.RequestException as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return

        try:
            from drift_reconciler.github_client_utils import resolve_repo_target
            from github import Github, Auth
            repo_slug, token, _branch = resolve_repo_target(scope)
            if not repo_slug or not token:
                self._json_error(502, f"No GitHub client resolved for scope '{scope}'.")
                return
            g = Github(auth=Auth.Token(token))
            pr = g.get_repo(repo_slug).get_pull(pr_number)

            commits = [{"sha": c.sha[:7], "message": (c.commit.message or "").split("\n")[0]}
                       for c in pr.get_commits()]
            files = [{"name": f.filename, "additions": f.additions,
                      "deletions": f.deletions, "status": f.status}
                     for f in pr.get_files()]
            checks = []
            try:
                head_commit = pr.get_commit(pr.head.sha)
                for run in head_commit.get_check_runs():
                    checks.append({"name": run.name, "conclusion": run.conclusion or "pending"})
            except Exception:
                checks = []

            payload = {
                "number": pr.number,
                "title": pr.title,
                "body": pr.body or "",
                "state": pr.state,
                "merged": pr.merged,
                "mergeable": pr.mergeable,
                "mergeable_state": pr.mergeable_state,
                "additions": pr.additions,
                "deletions": pr.deletions,
                "changed_files": pr.changed_files,
                "commits": commits,
                "files": files,
                "checks": checks,
                "html_url": pr.html_url,
            }
        except Exception as exc:
            self._json_error(502, f"Failed to fetch PR details: {exc}")
            return

        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_pending_apply_decision(self, path):
        """POST /api/pending-applies/{id}/decision

        Body: ``{"decision": "approved"|"rejected", "approved_by": "..."}``.
        Only valid while the row is still ``awaiting_approval`` — a second
        decision on the same row returns 409.  Flips the row's state and
        records who decided; triggers nothing else."""
        pending_id = path.split("/")[3]  # /api/pending-applies/{id}/decision

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        decision = body.get("decision", "")
        if decision not in ("approved", "rejected"):
            self._json_error(400, "decision must be 'approved' or 'rejected'.")
            return
        approved_by = (body.get("approved_by") or "").strip()
        if not approved_by:
            self._json_error(400, "approved_by is required.")
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json", "Prefer": "return=representation"}
        table_url = f"{url}/rest/v1/pending_applies"

        # ── Atomically claim the row first (compare-and-set) ────────────
        # The conditional UPDATE is the single source of truth for who
        # decided: a concurrent decision on the same row loses here and
        # 409s, so the GitHub merge/close below can only ever run once.
        payload = {
            "status": decision,
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            resp = requests.patch(
                f"{table_url}?id=eq.{pending_id}&status=eq.awaiting_approval",
                headers=headers, json=payload, timeout=10,
            )
            claimed = resp.json() if resp.text and resp.status_code == 200 else []
            if not claimed:
                self._json_error(409, "No awaiting_approval row matched — already decided or not found.")
                return
        except requests.RequestException as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return

        row = claimed[0] if isinstance(claimed, list) else claimed
        pr_number = row.get("pr_number")
        scope = row.get("scope")
        if not pr_number or not scope:
            self._json_error(409, "Pending apply row is missing pr_number or scope.")
            return

        def _rollback(reason: str) -> None:
            # GitHub call failed after the row was claimed — restore
            # awaiting_approval so the decision is retryable.
            print(f"  ⚠ Rolling back claim for PR #{pr_number} ({scope}): {reason}", file=sys.stderr)
            try:
                resp = requests.patch(
                    f"{table_url}?id=eq.{pending_id}",
                    headers=headers,
                    json={"status": "awaiting_approval", "approved_by": None, "approved_at": None},
                    timeout=10,
                )
                if resp.status_code >= 300:
                    # Rollback failed — the row stays approved/rejected
                    # with no job behind it.  Loud log; the operator must
                    # reset the row by hand.
                    print(f"  ❌ ROLLBACK FAILED ({resp.status_code}): {resp.text[:300]} — "
                          f"row {pending_id} is stuck in '{decision}'; reset manually", file=sys.stderr)
            except requests.RequestException as exc:
                print(f"  ❌ ROLLBACK FAILED: {exc} — row {pending_id} is stuck in "
                      f"'{decision}'; reset manually", file=sys.stderr)

        # ── Approved → merge the PR via the GitHub API ─────────────────
        # Row already claimed (status=approved); a merge failure rolls
        # the status back so the decision is retryable.
        if decision == "approved":
            try:
                from drift_reconciler.github_integration import merge_pr
                merge_result = merge_pr(scope, pr_number, commit_message=f"Merge drift fix PR #{pr_number}")
            except RuntimeError as exc:
                # Merge refused (conflict, branch protection, bad token, ...)
                _rollback(f"merge failed: {exc}")
                self._json_error(409, f"GitHub merge failed: {exc}")
                return

            if not (merge_result or {}).get("merged"):
                # merge_pr only raises on API errors; a non-merged result
                # (e.g. "already merged") must not proceed as approved.
                _rollback("GitHub did not merge the PR")
                self._json_error(409, "GitHub did not merge the PR.")
                return

            # Record merge info on the pending row so the gate-failure
            # revert path has the merge commit SHA available.
            merge_sha = (merge_result or {}).get("sha")
            if merge_sha:
                try:
                    requests.patch(
                        f"{table_url}?pr_number=eq.{pr_number}&scope=eq.{scope}",
                        headers=headers,
                        json={
                            "merged_at": datetime.now(timezone.utc).isoformat(),
                            "merge_commit_sha": merge_sha,
                        },
                        timeout=10,
                    )
                except requests.RequestException as exc:
                    print(f"  ⚠ pending row merge-info update failed: {exc}", file=sys.stderr)

            # NOTE: drift_events resolution is deliberately NOT done here.
            # agent.py resolves (approve) / marks reverted (reject) only
            # after the terraform apply itself succeeds, so a failed apply
            # leaves the events open.  Resolving pre-apply here would show
            # drift as fixed while AWS still differs from code.

        # ── Rejected → close the PR (no merge) ─────────────────────────
        # close_pr failure is BLOCKING: the PR stays open, so we must not
        # spawn the revert against a PR GitHub still tracks as open.  The
        # claim is rolled back so the row is retryable.
        elif decision == "rejected":
            try:
                from drift_reconciler.github_integration import close_pr
                close_pr(scope, pr_number)
            except RuntimeError as exc:
                _rollback(f"close failed: {exc}")
                self._json_error(409, f"GitHub close failed: {exc}")
                return

        # ── Spawn the apply/revert job asynchronously ─────────────────
        # Approve → --apply-pr against post-merge main.
        # Reject  → --revert-pr against pre-drift main (PR never merged),
        #           so the apply reverts AWS to match code.
        # (row/pr_number/scope come from the claim above.)
        if pr_number and scope:
            # Reuse the pending_applies row id as the log run_id so
            # the log viewer can be pointed at it later if desired.
            apply_run_id = row.get("id") or f"apply-{pr_number}-{int(datetime.now().timestamp())}"
            try:
                tf_dir = _tf_dir_for(scope)
            except RuntimeError as exc:
                print(f"  ⚠ Apply spawn aborted for {scope}: {exc}", file=sys.stderr)
                # Mark the row failed so the dashboard shows the true
                # state — the operator fixes the environment's repo
                # config and resets the row to retry.  Non-2xx so the
                # frontend never toasts success for an unstarted job.
                try:
                    requests.patch(
                        f"{table_url}?id=eq.{pending_id}",
                        headers=headers,
                        json={
                            "status": "failed",
                            "result": {"error": str(exc), "apply_started": False},
                        },
                        timeout=10,
                    )
                except requests.RequestException as patch_exc:
                    print(f"  ⚠ failed-row PATCH error: {patch_exc}", file=sys.stderr)
                self._json_error(500, f"Apply could not start: {exc}")
                return

            mode_flag = "--revert-pr" if decision == "rejected" else "--apply-pr"
            cmd = [
                _sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                mode_flag, str(pr_number),
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            _spawn_with_capture(cmd, apply_run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)
            print(f"  [apply] Spawned {decision} apply for PR #{pr_number} ({scope})", file=sys.stderr)

        data = json.dumps({"ok": True, "apply_started": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── GitHub webhook receiver ──────────────────────────────────────
    def _handle_github_webhook(self):
        """POST /api/webhooks/github

        Receives pull_request events from GitHub.  A PR that was merged
        (action=closed, merged=true) and has "Drift fix" in the title is
        resolved to an environment by matching the webhook's repository
        full_name against each environment's repo_url.  A matching
        environment's pending_applies row is inserted for dashboard-side
        approval.  Everything else is a silent 204 no-op.

        Authenticated by X-Hub-Signature-256 (HMAC-SHA256 over the raw
        body) using the environment's webhook_secret, or the global GitHub
        token (GITHUB_TOKEN env or app_settings.github_token) as fallback.

        If no environment matches the repo, or signature verification fails,
        returns 401 Unauthorized (same response for both, to avoid leaking
        which repos are configured).

        NOTE: this is a FALLBACK path.  The primary flow is the dashboard
        Approve button calling merge_pr() directly (which spawns apply and
        resolves drift_events itself).  This webhook only matters for
        out-of-band merges done manually on GitHub; in the common dashboard
        case the dedup-guarded pending_applies insert below is a no-op
        because the approve flow already transitioned the row.
        """
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""

        # ── Parse payload (unverified — only reads repo full_name) ─────
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid JSON body")
            return

        # Extract the unverified repo full_name (e.g. "owner/repo")
        repo_full_name = (payload.get("repository") or {}).get("full_name", "").strip()
        if not repo_full_name:
            self._json_error(401, "Unauthorized")  # No repo info = invalid webhook
            return

        # ── Resolve environment by repo_url match ──────────────────────
        # Fetch all active environments with repo_url set.
        url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url_base or not key:
            self._json_error(401, "Unauthorized")  # Supabase unavailable
            return

        headers_auth = {"apikey": key, "Authorization": f"Bearer {key}"}
        resolved_env = None
        resolved_env_id = None
        try:
            resp = requests.get(
                f"{url_base}/rest/v1/environments"
                f"?select=id,slug,repo_url&is_active=eq.true&repo_url=not.is.null",
                headers=headers_auth,
                timeout=10,
            )
            if resp.status_code == 200:
                envs = resp.json() if resp.text else []
                # Match repo_full_name against each environment's repo_url
                from drift_reconciler.github_client_utils import _parse_repo_url
                for env in envs:
                    parsed = _parse_repo_url(env.get("repo_url", ""))
                    if parsed and parsed.lower() == repo_full_name.lower():
                        resolved_env = env
                        resolved_env_id = env.get("id")
                        break
        except requests.RequestException:
            pass

        if not resolved_env:
            # No environment found for this repo — return 401 (don't leak)
            self._json_error(401, "Unauthorized")
            return

        # ── Fetch webhook_secret from environment_secrets (or fall back) ─
        webhook_secret = None
        try:
            resp = requests.get(
                f"{url_base}/rest/v1/environment_secrets"
                f"?select=webhook_secret&environment_id=eq.{resolved_env_id}",
                headers=headers_auth,
                timeout=10,
            )
            if resp.status_code == 200:
                rows = resp.json() if resp.text else []
                if rows:
                    webhook_secret = (rows[0].get("webhook_secret") or "").strip() or None
        except requests.RequestException:
            pass

        # Fall back to global GITHUB_TOKEN if no webhook_secret set
        if not webhook_secret:
            webhook_secret = os.environ.get("GITHUB_TOKEN", "").strip()
            if not webhook_secret:
                try:
                    from drift_reconciler.github_settings import get_github_token
                    webhook_secret = (get_github_token() or "").strip()
                except Exception:
                    webhook_secret = ""
        
        if not webhook_secret:
            # No secret available for verification
            self._json_error(401, "Unauthorized")
            return

        # ── Verify signature using the resolved secret ──────────────────
        sig_header = self.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            self._json_error(401, "Unauthorized")
            return

        expected = hmac.new(webhook_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        received = sig_header[len("sha256="):].strip()
        if not hmac.compare_digest(expected, received):
            self._json_error(401, "Unauthorized")
            return

        # ── Validate payload and extract PR details ─────────────────────
        if payload.get("action") != "closed":
            self._send_no_content()
            return

        pr = payload.get("pull_request") or {}
        if not pr.get("merged"):
            self._send_no_content()
            return

        title = pr.get("title", "")
        # Guard: title must contain "Drift fix" (confirms reconciler-created PR)
        if "Drift fix" not in title:
            self._send_no_content()
            return

        pr_number = pr.get("number")
        merged_at = pr.get("merged_at")
        merge_commit_sha = pr.get("merge_commit_sha") or ""
        if not pr_number:
            self._send_no_content()
            return

        # Use the resolved environment's slug as the scope
        scope = resolved_env.get("slug")
        if not scope:
            self._send_no_content()
            return

        # ── Insert pending_applies row ──────────────────────────────────
        # Skip if a row already exists for this (pr_number, scope) in ANY
        # status: the dashboard claim flips awaiting_approval → approved/
        # rejected BEFORE the merged webhook is delivered, so checking only
        # awaiting_approval would let a redelivery insert a duplicate.
        # A (pr_number, scope) unique index backs this up for concurrent
        # deliveries (see migrations/add_unique_pr_scope_to_pending_applies.sql).
        headers_body = {"apikey": key, "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json", "Prefer": "return=representation"}
        try:
            existing = requests.get(
                f"{url_base}/rest/v1/pending_applies"
                f"?select=id&pr_number=eq.{pr_number}&scope=eq.{scope}&limit=1",
                headers=headers_body, timeout=10,
            )
            if existing.status_code == 200 and existing.json():
                self._send_no_content()
                return

            resp = requests.post(
                f"{url_base}/rest/v1/pending_applies",
                headers=headers_body,
                json={
                    "pr_number": pr_number,
                    "scope": scope,
                    "status": "awaiting_approval",
                    "merged_at": merged_at,
                    "merge_commit_sha": merge_commit_sha or None,
                },
                timeout=10,
            )
            if resp.status_code == 409:
                # Unique (pr_number, scope) violation — a concurrent
                # delivery inserted the row first.  Same as a dedup hit.
                self._send_no_content()
                return
            if resp.status_code not in (200, 201):
                print(f"  [webhook] pending_applies insert failed ({resp.status_code}): "
                      f"{resp.text[:200]}", file=sys.stderr)
                self._json_error(502, "Failed to record pending apply")
                return
        except requests.RequestException as exc:
            print(f"  [webhook] Supabase unreachable: {exc}", file=sys.stderr)
            self._json_error(502, "Supabase unreachable")
            return

        data = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_no_content(self):
        self.send_response(204)
        self.end_headers()

    # ── Live log streaming ──────────────────────────────────────────
    def _serve_run_logs(self):
        """GET /api/scan/{run_id}/logs?offset={n}

        Returns newline-delimited log lines from *offset* onward as a
        JSON array of ``{n, ts, text}`` objects plus a ``complete`` flag
        sourced from the database (not inferred from emptiness)."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Extract run_id from /api/scan/{run_id}/logs
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) < 4:
            self._json_error(400, "Missing run_id in path")
            return
        run_id = parts[-2]  # /api/scan/{run_id}/logs → index -2

        try:
            offset = max(0, int(params.get("offset", ["0"])[0]))
        except (ValueError, IndexError):
            offset = 0

        # ── Read lines from log file (primary) or ring buffer (fallback) ──
        log_path = _LOG_DIR / f"{run_id}.log"
        all_lines: list[str] = []
        try:
            if log_path.is_file():
                with open(log_path, encoding="utf-8") as f:
                    all_lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            pass

        # Fallback to in-memory ring buffer when file is missing or unreadable.
        if not all_lines:
            with _LOG_LOCK:
                buf = _LOG_BUFFERS.get(run_id)
                if buf is not None:
                    all_lines = list(buf)

        # ── Slice from offset; parse ts + text ──
        result_lines = []
        for i in range(offset, len(all_lines)):
            raw = all_lines[i]
            ts = ""
            text = raw
            if raw.startswith("[") and "] " in raw:
                bracket_end = raw.index("] ")
                ts = raw[1:bracket_end]
                text = raw[bracket_end + 2:]
            result_lines.append({"n": i, "ts": ts, "text": text})

        # ── Determine completeness from the database ──
        complete = False
        try:
            url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            if url_base and key:
                headers = {"apikey": key, "Authorization": f"Bearer {key}"}
                for table in ("scan_runs", "rollback_runs", "pending_applies"):
                    try:
                        resp = requests.get(
                            f"{url_base}/rest/v1/{table}?select=status&id=eq.{run_id}",
                            headers=headers, timeout=5,
                        )
                        if resp.status_code == 200 and resp.json():
                            row = resp.json()[0]
                            status = row["status"]
                            if table == "pending_applies":
                                # Apply jobs: terminal states are applied /
                                # failed / cancelled / reverted_gate_blocked /
                                # manual_revert_required.
                                complete = status in (
                                    "applied", "failed", "cancelled",
                                    "reverted_gate_blocked", "manual_revert_required",
                                )
                            else:
                                complete = status in ("complete", "failed", "cancelled")
                            break
                    except requests.RequestException:
                        continue
        except Exception:
            # If Supabase is unreachable, treat as incomplete — the
            # caller will keep polling.
            pass

        payload = json.dumps(
            {"lines": result_lines, "complete": complete},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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


def main() -> int:
    _load_env()

    # ── Auth gate ──────────────────────────────────────────────────
    _api_token = os.environ.get("API_ACCESS_TOKEN", "").strip()
    if not _api_token:
        print("=" * 68)
        print("WARNING: API_ACCESS_TOKEN is not set.")
        print("The dashboard is running with NO authentication —")
        print("anyone who can reach this port can trigger scans,")
        print("execute rollbacks, and modify environments.")
        print(f"Set API_ACCESS_TOKEN in .env and restart.")
        print("=" * 68)

    parser = argparse.ArgumentParser(description="Serve the drift dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        return 1

    # ── Stale-run reconciliation ──────────────────────────────────────
    # Runs at startup and every 5 min on a background thread.  Marks any
    # scan_runs / rollback_runs row that's been "running" longer than the
    # threshold as failed, so orphaned rows don't stay stuck forever after
    # a process crash, manual kill, or server restart.
    _STALE_TIMEOUT_MINUTES = 30

    def _reconcile_stale_runs() -> int:
        """Mark scan_runs and rollback_runs rows that have been 'running'
        longer than _STALE_TIMEOUT_MINUTES as failed.  Returns the number
        of rows touched."""
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = (_dt.now(_tz.utc) - _td(minutes=_STALE_TIMEOUT_MINUTES)).isoformat()
        touched = 0
        headers = _supabase_headers()
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")

        for table, result_col in (("scan_runs", "result_summary"), ("rollback_runs", "result")):
            try:
                # Fetch running rows older than the cutoff.
                resp = requests.get(
                    f"{base}/rest/v1/{table}"
                    f"?select=id,started_at"
                    f"&status=eq.running"
                    f"&started_at=lt.{cutoff}",
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                rows = resp.json() if resp.text else []
                if not rows:
                    continue

                stale_ids = [r["id"] for r in rows]
                payload = {
                    "status": "failed",
                    "current_stage": "stale_timeout",
                    "completed_at": _dt.now(_tz.utc).isoformat(),
                    result_col: {
                        "summary": "This run never reported completion — the server "
                                   "may have been restarted or the agent process "
                                   "may have been killed before it could finish.",
                        "detail": "Run did not report completion — possibly crashed or interrupted",
                        "suggestion": "Retry the operation. If the issue persists, "
                                      "check server logs for the run ID above.",
                    },
                }
                # PATCH each row individually (PostgREST doesn't support
                # bulk PATCH with per-row values via in()).
                for rid in stale_ids:
                    try:
                        patch_resp = requests.patch(
                            f"{base}/rest/v1/{table}?id=eq.{rid}",
                            headers=headers,
                            json=payload,
                            timeout=10,
                        )
                        if patch_resp.status_code in (200, 204):
                            touched += 1
                    except requests.RequestException:
                        continue

                if stale_ids:
                    print(f"[stale-reconciler] Marked {len(stale_ids)} stale {table} row(s) as failed "
                          f"(running since before {cutoff[:19]}Z)")
            except Exception:
                continue

        return touched

    # Run once at startup.
    try:
        n = _reconcile_stale_runs()
        if n > 0:
            print(f"[stale-reconciler] Startup sweep: {n} stale row(s) reconciled")
    except Exception:
        pass

    # Background thread — re-check every 5 min.
    def _stale_reconciler_loop() -> None:
        import time as _time
        while True:
            _time.sleep(300)  # 5 min
            try:
                n = _reconcile_stale_runs()
                if n > 0:
                    print(f"[stale-reconciler] Periodic sweep: {n} stale row(s) reconciled")
            except Exception:
                pass

    _stale_thread = threading.Thread(target=_stale_reconciler_loop, daemon=True)
    _stale_thread.start()

    _cleanup_old_logs()

    print(f"Dashboard → http://localhost:{args.port}")
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), _Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
