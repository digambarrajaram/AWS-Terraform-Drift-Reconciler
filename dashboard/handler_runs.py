"""HTTP handler mixin: cancel + run log streaming."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests

from dashboard.process_runner import (
    _LOG_BUFFERS,
    _LOG_DIR,
    _LOG_LOCK,
    _RUNNING,
    _RUNNING_LOCK,
    _CANCELLED,
)


def _serve_requests():
    from dashboard import serve
    return serve.requests


class RunsMixin:
    def _cancel_run(self, path, table):
        """POST /api/scan/{run_id}/cancel, /api/rollback/{run_id}/cancel,
        or /api/pending-applies/{id}/cancel.

        Marks the run/pending row as 'cancelled' and terminates the
        subprocess gracefully (SIGTERM → 5 s wait → SIGKILL) so terraform
        can release its state lock.  pending_applies has no completed_at
        column — only scan_runs/rollback_runs get that field."""
        run_id = path.split("/")[3]  # /api/scan/{run_id}/cancel

        # Write the cancelled status to Supabase FIRST so _watch_exit
        # sees it and doesn't overwrite it with 'failed'.
        url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url_base or not key:
            self._json_error(502, "Supabase not configured")
            return
        cancel_body = {"status": "cancelled"}
        if table == "pending_applies":
            cancel_body["result"] = {
                "cancelled": True,
                "message": "Decision job cancelled from dashboard",
            }
        else:
            cancel_body["completed_at"] = datetime.now(timezone.utc).isoformat()
        status_filter = "in.(approved,rejected)" if table == "pending_applies" else "eq.running"
        cancel_url = f"{url_base}/rest/v1/{table}?id=eq.{run_id}&status={status_filter}"
        if getattr(self, "auth_user_id", None) and table in (
            "scan_runs", "rollback_runs", "pending_applies",
        ):
            cancel_url += f"&user_id=eq.{self.auth_user_id}"
        try:
            response = requests.patch(
                cancel_url,
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "Prefer": "return=representation"},
                json=cancel_body,
                timeout=5,
            )
        except requests.RequestException as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        if response.status_code not in (200, 204):
            self._json_error(502, f"Failed to cancel run ({response.status_code})")
            return
        if response.status_code == 200 and response.text and not response.json():
            self._json_error(409, "Run is no longer running")
            return

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
    def _serve_run_logs(self):
        requests = _serve_requests()
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
        status = None
        result_output = None
        try:
            url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            if url_base and key:
                headers = {"apikey": key, "Authorization": f"Bearer {key}"}
                # Route by path: the Approvals drawer polls pending-apply
                # ids via /api/pending-applies/, Scan/Rollback ids via
                # /api/scan/.  Probing every table per poll is 2-3
                # Supabase round-trips a request — fine once, but the
                # drawer polls every 800 ms, so scope the probe to the
                # table the id actually lives in.
                tables = ("pending_applies",) if "/api/pending-applies/" in parsed.path else ("scan_runs", "rollback_runs")
                for table in tables:
                    try:
                        # The drawer renders status from this same payload,
                        # so it has exactly one poller per row — the log
                        # poll.  select=status,result is pending-only: scan/
                        # rollback tables don't have a result column, and
                        # PostgREST 400s on unknown columns.
                        sel = "status,result" if table == "pending_applies" else "status"
                        resp = requests.get(
                            f"{url_base}/rest/v1/{table}?select={sel}&id=eq.{run_id}",
                            headers=headers, timeout=5,
                        )
                        if resp.status_code == 200 and resp.json():
                            row = resp.json()[0]
                            status = row["status"]
                            if table == "pending_applies":
                                # Apply jobs: terminal states are applied /
                                # failed / cancelled / reverted (file-only
                                # revert) / reverted_gate_blocked /
                                # manual_revert_required.  The claim states
                                # 'approved'/'rejected' are NOT terminal.
                                complete = status in (
                                    "applied", "failed", "cancelled", "reverted",
                                    "reverted_gate_blocked", "manual_revert_required",
                                    "excepted",
                                )
                                result = row.get("result") or {}
                                result_output = result.get("output") if isinstance(result, dict) else None
                            else:
                                complete = status in ("complete", "failed", "cancelled")
                            break
                    except requests.RequestException:
                        continue
        except Exception:
            # If Supabase is unreachable, treat as incomplete — the
            # caller will keep polling.
            pass

        # The log file dies with the process (startup purge, restart) while
        # the row's result lives in the DB — a finished file-only apply that
        # wrote its whole log as one output line must still render something
        # after a dashboard restart.  Only when the file is gone AND the row
        # is terminal, so this never masks live-but-empty logs.
        if not result_lines and complete and result_output:
            result_lines.append({"n": 0, "ts": "", "text": result_output})

        payload = json.dumps(
            {"lines": result_lines, "complete": complete, "status": status},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

