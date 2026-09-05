"""Dashboard server boot + stale-run reconciliation."""
from __future__ import annotations

import argparse
import http.server
import logging
import os
import sys
import threading
from pathlib import Path

import requests

from dashboard.paths import _REPO_ROOT, _DASHBOARD_DIR
from dashboard.process_runner import _cleanup_old_logs
from dashboard.supabase_http import _supabase_headers

logger = logging.getLogger(__name__)

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



def main() -> int:
    from dashboard.serve import _Handler  # composed handler facade
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from drift_reconciler.environment_credentials import (
        _backend_aws_credential_mode,
        verify_clone_base_writable,
    )
    logger.info("AWS backend credential mode: %s", _backend_aws_credential_mode())

    try:
        clone_base = verify_clone_base_writable()
        logger.info("Git clone directory writable: %s", clone_base)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    # ── Auth gate ──────────────────────────────────────────────────
    _session_secret = os.environ.get("SESSION_SECRET", "").strip()
    if not _session_secret:
        print("SESSION_SECRET must be set in .env (HMAC key for session cookies)")
        return 1
    if len(_session_secret) < 32:
        print("SESSION_SECRET must be at least 32 characters")
        return 1
    os.environ["SESSION_SECRET"] = _session_secret

    parser = argparse.ArgumentParser(description="Serve the drift dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1; use a reverse proxy for external access)",
    )
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

    def _reconcile_stale_drift_events() -> int:
        """Sync open drift_events rows that carry a PR number against the
        PR's actual GitHub state (open/closed/merged).

        Backstop for every close/merge action: an out-of-band close, a
        dead apply job, or a missed webhook must not leave a row 'open'
        once GitHub has resolved the PR.  Rows whose state is already
        terminal, or whose PR is still open, are no-ops.  Returns the
        number of rows synced."""
        base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        headers = _supabase_headers()
        if not base or not headers.get("apikey"):
            return 0
        try:
            resp = requests.get(
                f"{base}/rest/v1/drift_events"
                f"?select=pr_number,account"
                f"&status=eq.open"
                f"&not.pr_number=is.null"
                f"&limit=50",
                headers=headers, timeout=10,
            )
            if resp.status_code != 200:
                return 0
            rows = resp.json() if resp.text else []
        except (requests.RequestException, ValueError):
            return 0
        from drift_reconciler.drift_history import sync_pr_state
        synced = 0
        for row in rows:
            try:
                state = sync_pr_state(row["pr_number"], row["account"])
                if state in ("closed", "merged"):
                    synced += 1
            except Exception:
                continue
        return synced

    # Run once at startup.
    try:
        n = _reconcile_stale_runs()
        if n > 0:
            print(f"[stale-reconciler] Startup sweep: {n} stale row(s) reconciled")
        dn = _reconcile_stale_drift_events()
        if dn > 0:
            print(f"[stale-reconciler] Startup sweep: {dn} drift event(s) synced to GitHub PR state")
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
                dn = _reconcile_stale_drift_events()
                if dn > 0:
                    print(f"[stale-reconciler] Periodic sweep: {dn} drift event(s) synced to GitHub PR state")
            except Exception:
                pass

    _stale_thread = threading.Thread(target=_stale_reconciler_loop, daemon=True)
    _stale_thread.start()

    _cleanup_old_logs()

    print(f"Dashboard → http://{args.host}:{args.port}")
    if args.host in ("127.0.0.1", "localhost"):
        print("Bound to loopback only — put a TLS reverse proxy in front for external access.")
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), _Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
