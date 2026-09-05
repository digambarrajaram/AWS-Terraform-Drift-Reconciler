"""HTTP handler base: auth, routing, static file helpers."""
from __future__ import annotations

import hashlib
import hmac
import http.cookies
import http.server
import json
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import jwt
import requests
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

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
from dashboard.supabase_http import _supabase_get

_JWKS_CLIENT_LOCK = threading.Lock()
_jwks_client: PyJWKClient | None = None
_jwks_client_url: str | None = None
_JWKS_CACHE_TTL_SEC = 600  # 10 minutes

_SESSION_TTL_SEC = 3600
_SESSION_COOKIE = "session"
_CSRF_COOKIE = "csrf"
_CSRF_HEADER = "X-CSRF-Token"

# Public paths that skip session auth (UI shell + login bootstrap + static).
_PUBLIC_EXACT = frozenset({"/login", "/api/login", "/api/config"})
_PUBLIC_PREFIXES = ("/static/",)

# In-memory sliding-window rate limit for auth endpoints (per client IP).
_AUTH_RATE_LOCK = threading.Lock()
_AUTH_RATE_HITS: dict[str, deque[float]] = defaultdict(deque)
_AUTH_RATE_WINDOW_SEC = 60
_AUTH_RATE_MAX = 20


def _supabase_jwks_url() -> str | None:
    """Derive Supabase JWKS endpoint from SUPABASE_URL (no hardcoded project ref)."""
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/auth/v1/.well-known/jwks.json"


def _get_jwks_client() -> PyJWKClient | None:
    """Return a cached PyJWKClient for the current SUPABASE_URL JWKS endpoint."""
    global _jwks_client, _jwks_client_url
    url = _supabase_jwks_url()
    if not url:
        return None
    with _JWKS_CLIENT_LOCK:
        if _jwks_client is None or _jwks_client_url != url:
            _jwks_client = PyJWKClient(
                url,
                cache_jwk_set=True,
                lifespan=_JWKS_CACHE_TTL_SEC,
            )
            _jwks_client_url = url
    return _jwks_client


def _decode_supabase_jwt(token: str) -> dict | None:
    """Verify ES256 Supabase session JWT via JWKS; return payload or None."""
    client = _get_jwks_client()
    if client is None:
        return None

    refreshed = False
    while True:
        try:
            signing_key = client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256"],
                audience="authenticated",
            )
        except PyJWKClientError:
            if refreshed:
                return None
            client.get_signing_keys(refresh=True)
            refreshed = True
        except PyJWTError:
            return None


def _session_secret() -> bytes:
    """Return SESSION_SECRET bytes (required at startup — never empty here)."""
    return os.environ["SESSION_SECRET"].strip().encode("utf-8")


def _sign_session(user_id: str, exp: int) -> str:
    """Build HMAC-signed session token: user_id.exp.sig."""
    payload = f"{user_id}.{exp}"
    sig = hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_session_token(token: str) -> str | None:
    """Validate session token; return user_id or None."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, exp_s, sig = parts
    if not user_id or not exp_s or not sig:
        return None
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    expected = hmac.new(
        _session_secret(), f"{user_id}.{exp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    return user_id


def _auth_rate_allow(ip: str) -> bool:
    """Sliding-window rate limit for auth endpoints. True if request allowed."""
    now = time.time()
    with _AUTH_RATE_LOCK:
        hits = _AUTH_RATE_HITS[ip]
        while hits and hits[0] <= now - _AUTH_RATE_WINDOW_SEC:
            hits.popleft()
        if len(hits) >= _AUTH_RATE_MAX:
            return False
        hits.append(now)
        return True


class HandlerBase(http.server.SimpleHTTPRequestHandler):
    _CACHEABLE = {".js", ".css", ".png", ".svg", ".woff2"}
    _STATIC_EXTENSIONS = (
        ".js", ".css", ".png", ".svg", ".woff2", ".woff", ".ico", ".map",
        ".webp", ".gif", ".json",
    )

    def __init__(self, *args, **kwargs):
        self.auth_user_id: str | None = None
        super().__init__(*args, **kwargs)

    def _client_ip(self) -> str:
        # Prefer direct peer; X-Forwarded-For only when behind a trusted proxy.
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return self.client_address[0] if self.client_address else "unknown"

    def _parse_cookies(self) -> http.cookies.SimpleCookie:
        raw = self.headers.get("Cookie") or ""
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw)
        except http.cookies.CookieError:
            pass
        return jar

    def _cookie_value(self, name: str) -> str:
        jar = self._parse_cookies()
        morsel = jar.get(name)
        return morsel.value if morsel else ""

    def _cookie_secure(self) -> bool:
        """Secure cookies require HTTPS — skip on plain HTTP (e.g. EC2 IP:port)."""
        explicit = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
        if explicit in ("0", "false", "no"):
            return False
        if explicit in ("1", "true", "yes"):
            return True
        forwarded = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if forwarded == "https":
            return True
        app_url = os.environ.get("PUBLIC_APP_URL", "").strip().lower()
        return app_url.startswith("https://")

    def _set_cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int,
        httponly: bool,
    ) -> None:
        flags = f"{name}={value}; Path=/; Max-Age={max_age}; SameSite=Strict"
        if self._cookie_secure():
            flags += "; Secure"
        if httponly:
            flags += "; HttpOnly"
        self.send_header("Set-Cookie", flags)

    def _clear_cookie(self, name: str, *, httponly: bool) -> None:
        flags = f"{name}=; Path=/; Max-Age=0; SameSite=Strict"
        if self._cookie_secure():
            flags += "; Secure"
        if httponly:
            flags += "; HttpOnly"
        self.send_header("Set-Cookie", flags)

    def _is_public_path(self, path: str) -> bool:
        if path in _PUBLIC_EXACT:
            return True
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return True
        if path == "/static":
            return True
        # Static asset files (JS/CSS/fonts/images) — treat as /static allowlist.
        if path.endswith(self._STATIC_EXTENSIONS) and not path.startswith("/api/"):
            return True
        return False

    def _check_session(self) -> bool:
        """Verify session cookie and attach ``auth_user_id``. Return True if valid."""
        self.auth_user_id = None
        token = self._cookie_value(_SESSION_COOKIE)
        if not token:
            return False
        user_id = _verify_session_token(token)
        if not user_id:
            return False
        self.auth_user_id = user_id
        return True

    def _check_jwt(self) -> bool:
        """Verify Supabase Auth JWT (ES256 via JWKS) and attach ``auth_user_id``.

        Used by ``/api/login`` to exchange a valid Supabase credential for a
        session cookie. Requires ``Authorization: Bearer`` with aud=authenticated.
        """
        self.auth_user_id = None
        if not _supabase_jwks_url():
            return False
        auth_header = (self.headers.get("Authorization") or "").strip()
        if not auth_header.lower().startswith("bearer "):
            return False
        token = auth_header[7:].strip()
        if not token:
            return False
        payload = _decode_supabase_jwt(token)
        if not payload:
            return False
        sub = payload.get("sub")
        if not sub or not isinstance(sub, str):
            return False
        self.auth_user_id = sub
        return True

    def _check_csrf(self) -> bool:
        """Double-submit CSRF: cookie value must match request header."""
        cookie_tok = self._cookie_value(_CSRF_COOKIE)
        header_tok = (self.headers.get(_CSRF_HEADER) or "").strip()
        if not cookie_tok or not header_tok:
            return False
        return hmac.compare_digest(cookie_tok, header_tok)

    def _unauthorized(self) -> None:
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forbidden_csrf(self) -> None:
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rate_limited(self) -> None:
        body = json.dumps({"error": "too many requests"}).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_api_auth(self, *, require_csrf: bool = False) -> bool:
        """Central session gate for /api/* — call before every API dispatch."""
        if not self._check_session():
            self._unauthorized()
            return False
        if require_csrf and not self._check_csrf():
            self._forbidden_csrf()
            return False
        return True

    def _handle_api_login(self) -> None:
        """POST /api/login — verify Supabase JWT, issue session + CSRF cookies."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}

        if isinstance(body, dict) and body.get("logout") is True:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._clear_cookie(_SESSION_COOKIE, httponly=True)
            self._clear_cookie(_CSRF_COOKIE, httponly=False)
            resp = b'{"ok":true}'
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        if not self._check_jwt():
            if not _auth_rate_allow(self._client_ip()):
                self._rate_limited()
                return
            self._unauthorized()
            return

        assert self.auth_user_id is not None
        exp = int(time.time()) + _SESSION_TTL_SEC
        session_tok = _sign_session(self.auth_user_id, exp)
        csrf_tok = secrets.token_urlsafe(32)

        resp = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self._set_cookie(_SESSION_COOKIE, session_tok, max_age=_SESSION_TTL_SEC, httponly=True)
        self._set_cookie(_CSRF_COOKIE, csrf_tok, max_age=_SESSION_TTL_SEC, httponly=False)
        self.end_headers()
        self.wfile.write(resp)

    def _ownership_enforced(self) -> bool:
        """True when a verified session user is attached."""
        return bool(self.auth_user_id)

    def _owned_scopes(self) -> set[str]:
        """Active environment slugs owned by the current user.

        When JWT auth is disabled (no auth_user_id), falls back to all active
        scopes so local/dev without Supabase Auth keeps working.
        """
        if not self._ownership_enforced():
            return set(_get_valid_scopes())
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            return set()
        try:
            resp = requests.get(
                f"{base}/rest/v1/environments"
                f"?select=slug&user_id=eq.{self.auth_user_id}&is_active=eq.true",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code != 200:
                return set()
            return {row["slug"] for row in (resp.json() or []) if row.get("slug")}
        except (requests.RequestException, ValueError, TypeError):
            return set()

    def _require_owned_scope(self, scope: str) -> bool:
        """Validate *scope* exists and is owned by auth_user_id. Sends error on failure."""
        if not scope:
            self._json_error(400, "A valid scope is required")
            return False
        owned = self._owned_scopes()
        if scope in owned:
            return True
        if scope in _get_valid_scopes():
            self._json_error(403, "Forbidden — environment not owned by this user")
            return False
        self._json_error(
            400,
            f"Invalid scope: {scope}. Must be one of: " + ", ".join(sorted(owned)) + ".",
        )
        return False


    def do_GET(self):
        path = self.path.split("?")[0]

        # Central session gate for every /api/* route (allowlist: /api/login,
        # /api/config for login bootstrap — anon key is public by design).
        if path.startswith("/api/") and not self._is_public_path(path):
            if not self._require_api_auth():
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
        elif path == "/api/overview":
            self._serve_overview()
        elif path == "/api/scan-runs":
            self._serve_scan_runs()
        elif path.startswith("/api/scan-runs/"):
            self._serve_scan_run()
        elif path == "/api/pr-queue":
            self._serve_pr_queue()
        elif path == "/api/rollback-data":
            self._serve_rollback_data()
        elif path.startswith("/api/rollback-runs/"):
            self._serve_rollback_run()
        elif path == "/api/trends":
            self._serve_trends()
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
        elif path == "/api/routing-rules":
            self._serve_routing_rules()
        elif path == "/api/github-settings":
            self._serve_github_settings()
        elif path.startswith("/api/exceptions"):
            self._serve_api_exceptions()
        elif path.startswith("/api/scan/") and path.endswith("/logs"):
            self._serve_run_logs()
        elif path.endswith(self._STATIC_EXTENSIONS):
            self._serve_static(path)
        elif path.startswith("/api/"):
            self.send_error(404)
        else:
            self._serve_spa_index()

    def do_POST(self):
        path = self.path.split("?")[0]

        # GitHub webhook — authenticated by X-Hub-Signature-256, not the
        # dashboard session.  Must run BEFORE the session gate.
        if path == "/api/webhooks/github":
            self._handle_github_webhook()
            return

        if path == "/api/login":
            self._handle_api_login()
            return

        if path.startswith("/api/") and not self._require_api_auth(require_csrf=True):
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
            if not self._require_owned_scope(scope):
                return

            # Reject local overlaps before inserting a run row.
            with _RUNNING_LOCK:
                if any(p.poll() is None and sc == scope for p, _e, sc in _RUNNING.values()):
                    self._json_error(409, f"Scan already running for {scope}")
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
                tf_dir = _tf_dir_for(scope)
            except RuntimeError as se:
                self._json_error(400, f"Cannot resolve Terraform directory: {se}")
                return

            try:
                run_id = create_scan_run(scope, unmanaged_flag=False, scan_type="trivy_only", user_id=self.auth_user_id)
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            cmd = [
                sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--run-id", run_id,
                "--trivy-only",
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            try:
                _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)
            except Exception as se:
                try:
                    update_scan_run(
                        run_id, status="failed",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        result_summary={"summary": str(se)},
                    )
                except Exception as cleanup_error:
                    print(f"[scan] Failed to mark startup error for {run_id}: {cleanup_error}", file=sys.stderr)
                self._json_error(500, f"Failed to start scan: {se}")
                return

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
            if not self._require_owned_scope(scope):
                return

            # Reject local overlaps before inserting a run row.
            with _RUNNING_LOCK:
                if any(p.poll() is None and sc == scope for p, _e, sc in _RUNNING.values()):
                    self._json_error(409, f"Scan already running for {scope}")
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

            scan_mode = body.get("scan_mode", "drift_only")
            if scan_mode not in ("drift_only", "drift_and_unmanaged", "unmanaged_only"):
                self._json_error(400, "scan_mode must be drift_only, drift_and_unmanaged, or unmanaged_only.")
                return
            unmanaged_flag_for_db = scan_mode in ("drift_and_unmanaged", "unmanaged_only")
            try:
                tf_dir = _tf_dir_for(scope)
            except RuntimeError as se:
                self._json_error(400, f"Cannot resolve Terraform directory: {se}")
                return
            try:
                run_id = create_scan_run(scope, unmanaged_flag_for_db, scan_type=scan_mode, user_id=self.auth_user_id)
            except Exception as se:
                self._json_error(502, f"Failed to create scan run: {se}")
                return

            # Non-blocking subprocess — fire and respond 202 immediately.
            cmd = [
                sys.executable,
                str(_REPO_ROOT / "drift_reconciler" / "agent.py"),
                "--tf-dir", tf_dir,
                "--account-label", scope,
                "--run-id", run_id,
                "--scan-mode", scan_mode,
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            _configure_aws_env(env, scope)
            try:
                _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)
            except Exception as se:
                try:
                    update_scan_run(
                        run_id, status="failed",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        result_summary={"summary": str(se)},
                    )
                except Exception as cleanup_error:
                    print(f"[scan] Failed to mark startup error for {run_id}: {cleanup_error}", file=sys.stderr)
                self._json_error(500, f"Failed to start scan: {se}")
                return

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

            if not pr_number:
                self._json_error(400, "pr_number and a valid owned scope are required")
                return
            if not self._require_owned_scope(scope):
                return

            with _RUNNING_LOCK:
                if any(p.poll() is None and sc == scope for p, _e, sc in _RUNNING.values()):
                    self._json_error(409, f"Rollback already running for {scope}")
                    return
            try:
                resp = _supabase_get(
                    "rollback_runs",
                    {"select": "id", "pr_number": f"eq.{pr_number}", "scope": f"eq.{scope}", "status": "eq.running", "limit": "1"},
                )
                if resp.status_code == 200 and resp.json():
                    self._json_error(409, f"Rollback already running for PR #{pr_number}", run_id=resp.json()[0]["id"])
                    return
            except requests.RequestException as exc:
                self._json_error(502, f"Supabase unreachable: {exc}")
                return
            try:
                tf_dir = _tf_dir_for(scope)
            except RuntimeError as exc:
                self._json_error(400, f"Cannot resolve Terraform directory: {exc}")
                return

            try:
                run_id = create_rollback_run(pr_number, scope, mode="preview", user_id=self.auth_user_id)
            except Exception as se:
                self._json_error(502, f"Failed to create rollback run: {se}")
                return

            cmd = [
                sys.executable,
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
            try:
                _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)
            except Exception as exc:
                try:
                    from drift_reconciler.rollback_runs import update_rollback_run
                    update_rollback_run(run_id, status="failed", completed_at=datetime.now(timezone.utc).isoformat(), result={"summary": str(exc)})
                except Exception as cleanup_error:
                    print(f"[rollback] Failed to mark startup error for {run_id}: {cleanup_error}", file=sys.stderr)
                self._json_error(500, f"Failed to start rollback preview: {exc}")
                return

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

            if not pr_number:
                self._json_error(400, "pr_number and a valid owned scope are required")
                return
            if not self._require_owned_scope(scope):
                return

            with _RUNNING_LOCK:
                if any(p.poll() is None and sc == scope for p, _e, sc in _RUNNING.values()):
                    self._json_error(409, f"Rollback already running for {scope}")
                    return

            # Concurrency check — only one rollback for a given PR at a time.
            try:
                resp = _supabase_get(
                    "rollback_runs",
                    {"select": "id", "pr_number": f"eq.{pr_number}", "scope": f"eq.{scope}", "status": "eq.running", "limit": "1"}
                )
                if resp.status_code == 200 and resp.json():
                    existing_id = resp.json()[0]["id"]
                    self._json_error(409, f"Rollback already running for PR #{pr_number}", run_id=existing_id)
                    return
            except requests.RequestException as exc:
                self._json_error(502, f"Supabase unreachable: {exc}")
                return

            try:
                tf_dir = _tf_dir_for(scope)
            except RuntimeError as se:
                self._json_error(400, f"Cannot resolve Terraform directory: {se}")
                return
            try:
                run_id = create_rollback_run(pr_number, scope, mode="execute", user_id=self.auth_user_id)
            except Exception as se:
                self._json_error(502, f"Failed to create rollback run: {se}")
                return

            cmd = [
                sys.executable,
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
            try:
                _spawn_with_capture(cmd, run_id, env=env, cwd=str(_REPO_ROOT), scope=scope)
            except Exception as exc:
                try:
                    from drift_reconciler.rollback_runs import update_rollback_run
                    update_rollback_run(run_id, status="failed", completed_at=datetime.now(timezone.utc).isoformat(), result={"summary": str(exc)})
                except Exception as cleanup_error:
                    print(f"[rollback] Failed to mark startup error for {run_id}: {cleanup_error}", file=sys.stderr)
                self._json_error(500, f"Failed to start rollback execution: {exc}")
                return

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

    def do_PUT(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not self._require_api_auth(require_csrf=True):
            return
        self.send_error(404)

    def do_PATCH(self):
        path = self.path.split("?")[0]

        if not self._require_api_auth(require_csrf=True):
            return

        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_patch(env_id)
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]

        if not self._require_api_auth(require_csrf=True):
            return

        if path.startswith("/api/environments/"):
            env_id = path.split("/")[-1]
            self._handle_environments_delete(env_id)
        else:
            self.send_error(404)

    def end_headers(self):
        # Malformed requests (e.g. raw TLS on the HTTP port) may reach here
        # before ``self.path`` is set — guard to avoid AttributeError.
        path = getattr(self, "path", "") or ""
        ext = os.path.splitext(path.split("?")[0])[1]
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
        ctype = {
            ".js": "application/javascript",
            ".css": "text/css",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ico": "image/x-icon",
            ".json": "application/json",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_spa_index(self) -> None:
        """SPA fallback — serve index.html for client-side routes (e.g. /reset-password)."""
        fpath = _DASHBOARD_DIR / "index.html"
        if not fpath.is_file():
            self.send_error(404)
            return
        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_overview(self):
        """Return scope-validated summary data for the Overview page."""
        scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return

        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}

        try:
            severity_resp = requests.get(
                f"{base}/rest/v1/drift_severity_summary"
                f"?select=severity,count&account=eq.{scope}",
                headers=headers, timeout=10,
            )
            rollback_resp = requests.get(
                f"{base}/rest/v1/drift_events"
                f"?select=id&status=eq.open&pr_type=eq.rollback&account=eq.{scope}",
                headers={**headers, "Prefer": "count=exact"}, timeout=10,
            )
            scan_resp = requests.get(
                f"{base}/rest/v1/scan_runs"
                f"?select=completed_at&scope=eq.{scope}&status=eq.complete"
                f"&completed_at=not.is.null&order=completed_at.desc&limit=1",
                headers=headers, timeout=10,
            )
            cost_resp = requests.get(
                f"{base}/rest/v1/drift_events"
                f"?select=cost_impact&status=eq.open&account=eq.{scope}",
                headers=headers, timeout=10,
            )
            responses = (severity_resp, rollback_resp, scan_resp, cost_resp)
            if any(resp.status_code != 200 for resp in responses):
                self._json_error(502, "Overview query failed")
                return

            cost = 0
            for row in cost_resp.json() or []:
                value = row.get("cost_impact")
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = None
                estimate = value.get("monthly_estimate_usd") if isinstance(value, dict) else None
                if isinstance(estimate, (int, float)):
                    cost += estimate

            payload = {
                "severity": severity_resp.json() or [],
                "rollback_count": len(rollback_resp.json() or []),
                "last_scan": (scan_resp.json() or [{}])[0].get("completed_at"),
                "cost_impact": cost,
            }
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return

        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_scan_runs(self):
        """Return scan history for one validated active environment."""
        scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        try:
            resp = requests.get(
                f"{base}/rest/v1/scan_runs?select=*&scope=eq.{scope}"
                + (f"&user_id=eq.{self.auth_user_id}" if self._ownership_enforced() else "")
                + "&order=started_at.desc&limit=20",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code != 200:
                self._json_error(502, f"Scan history query failed ({resp.status_code})")
                return
            data = json.dumps(resp.json() or []).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_scan_run(self):
        """Return one scan run only after validating its requested scope."""
        parts = urlparse(self.path).path.rstrip("/").split("/")
        if len(parts) != 4 or not parts[3]:
            self._json_error(400, "Missing scan run id")
            return
        run_id = parts[3]
        scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        try:
            resp = requests.get(
                f"{base}/rest/v1/scan_runs?select=*&id=eq.{run_id}&scope=eq.{scope}"
                + (f"&user_id=eq.{self.auth_user_id}" if self._ownership_enforced() else "")
                + "&limit=1",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code != 200:
                self._json_error(502, f"Scan run query failed ({resp.status_code})")
                return
            rows = resp.json() or []
            if not rows:
                self._json_error(404, "Scan run not found")
                return
            data = json.dumps(rows[0]).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_pr_queue(self):
        """Return filtered, paginated PR queue data for a validated scope."""
        params = parse_qs(urlparse(self.path).query)
        scope = params.get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return
        try:
            page = max(0, int(params.get("page", ["0"])[0]))
        except ValueError:
            page = 0
        sort_column = params.get("sort", ["created_at"])[0]
        sort_ascending = params.get("ascending", ["false"])[0].lower() == "true"
        if sort_column not in ("created_at", "severity", "resource_id"):
            self._json_error(400, "Invalid sort column")
            return

        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        query = {"account": f"eq.{scope}"}
        for name, column in (("status", "status"), ("severity", "severity"), ("type", "pr_type")):
            value = params.get(name, ["all"])[0]
            if value != "all":
                query[column] = f"eq.{value}"
        search = params.get("search", [""])[0]
        if search:
            query["resource_id"] = f"ilike.*{search}*"
        date_from = params.get("dateFrom", [""])[0]
        date_to = params.get("dateTo", [""])[0]
        if date_from:
            query["created_at.gte"] = date_from
        if date_to:
            query["created_at.lte"] = f"{date_to}T23:59:59"
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
        try:
            if sort_column == "severity":
                rows = []
                offset = 0
                while True:
                    batch_query = {"select": "*", **query, "offset": str(offset), "limit": "1000"}
                    resp = requests.get(
                        f"{base}/rest/v1/drift_events", params=batch_query,
                        headers=headers, timeout=10,
                    )
                    if resp.status_code != 200:
                        break
                    batch = resp.json() or []
                    rows.extend(batch)
                    if len(batch) < 1000:
                        break
                    offset += 1000
                rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
                rows.sort(key=lambda row: (rank.get(str(row.get("severity", "")).upper(), 4), row.get("created_at", "")), reverse=not sort_ascending)
                total = len(rows)
                rows = rows[page * 20:(page + 1) * 20]
            else:
                query.update({"order": f"{sort_column}.{'asc' if sort_ascending else 'desc'}", "offset": str(page * 20), "limit": "20"})
                resp = requests.get(
                    f"{base}/rest/v1/drift_events", params={"select": "*", **query},
                    headers=headers, timeout=10,
                )
                rows = resp.json() if resp.status_code == 200 else []
                content_range = resp.headers.get("Content-Range", "*/0")
                total = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else len(rows)
            if resp.status_code != 200:
                self._json_error(502, f"PR queue query failed ({resp.status_code})")
                return
            data = json.dumps({"events": rows, "count": total}).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _rollback_scope(self):
        scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return None
        return scope

    def _serve_rollback_data(self):
        scope = self._rollback_scope()
        if not scope:
            return
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        try:
            eligible = requests.get(
                f"{base}/rest/v1/drift_events?select=*&account=eq.{scope}"
                "&status=in.(open,resolved)&changes_jsonb=not.is.null"
                "&pr_number=not.is.null&order=created_at.desc",
                headers=headers, timeout=10,
            )
            history = requests.get(
                f"{base}/rest/v1/rollback_runs?select=*&scope=eq.{scope}"
                + (f"&user_id=eq.{self.auth_user_id}" if self._ownership_enforced() else "")
                + "&order=started_at.desc",
                headers=headers, timeout=10,
            )
            if eligible.status_code != 200 or history.status_code != 200:
                self._json_error(502, "Rollback data query failed")
                return
            data = json.dumps({
                "eligible": eligible.json() or [],
                "history": history.json() or [],
            }).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_rollback_run(self):
        parts = urlparse(self.path).path.rstrip("/").split("/")
        if len(parts) != 4 or not parts[3]:
            self._json_error(400, "Missing rollback run id")
            return
        scope = self._rollback_scope()
        if not scope:
            return
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        try:
            resp = requests.get(
                f"{base}/rest/v1/rollback_runs?select=*&id=eq.{parts[3]}&scope=eq.{scope}"
                + (f"&user_id=eq.{self.auth_user_id}" if self._ownership_enforced() else "")
                + "&limit=1",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=10,
            )
            if resp.status_code != 200:
                self._json_error(502, f"Rollback run query failed ({resp.status_code})")
                return
            rows = resp.json() or []
            if not rows:
                self._json_error(404, "Rollback run not found")
                return
            data = json.dumps(rows[0]).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_trends(self):
        """Return all Trends data for one validated scope and period."""
        params = parse_qs(urlparse(self.path).query)
        scope = params.get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return
        try:
            days = int(params.get("days", ["30"])[0])
        except ValueError:
            self._json_error(400, "days must be 7, 30, or 90")
            return
        if days not in (7, 30, 90):
            self._json_error(400, "days must be 7, 30, or 90")
            return

        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        rpc_headers = {**headers, "Content-Type": "application/json"}
        try:
            rpc_results = {}
            for name in ("get_most_drifted", "get_mttr_by_severity", "get_drift_volume_daily"):
                resp = requests.post(
                    f"{base}/rest/v1/rpc/{name}", headers=rpc_headers,
                    json={"p_account": scope, "p_days": days}, timeout=10,
                )
                if resp.status_code != 200:
                    self._json_error(502, f"Trends query failed ({resp.status_code})")
                    return
                rpc_results[name] = resp.json() or []

            since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()

            def fetch_events(extra=None):
                query = {"account": f"eq.{scope}", "created_at": f"gte.{since}"}
                query.update(extra or {})
                return requests.get(
                    f"{base}/rest/v1/drift_events", params={"select": "resource_id", **query},
                    headers=headers, timeout=10,
                )

            responses = [
                fetch_events(),
                fetch_events({"status": "eq.resolved"}),
                fetch_events({"status": "eq.open"}),
                fetch_events({"pr_type": "eq.rollback"}),
            ]
            if any(resp.status_code != 200 for resp in responses):
                self._json_error(502, "Trends summary query failed")
                return
            all_rows, resolved_rows, open_rows, rollback_rows = [resp.json() or [] for resp in responses]
            data = json.dumps({
                "most_drifted": rpc_results["get_most_drifted"],
                "mttr": rpc_results["get_mttr_by_severity"],
                "volume": rpc_results["get_drift_volume_daily"],
                "summary": {
                    "total": len(all_rows),
                    "uniqueResources": len({row.get("resource_id") for row in all_rows}),
                    "resolved": len(resolved_rows),
                    "open": len(open_rows),
                    "rollback": len(rollback_rows),
                },
            }).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Cancel in-progress run ─────────────────────────────────────
