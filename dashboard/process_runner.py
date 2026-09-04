"""Subprocess spawn, live log capture, and cancel-side TF unlock."""
from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

from dashboard.env import _get_env_field

_LOG_DIR = Path("/tmp/drift-logs")
_LOG_BUFFERS: dict[str, deque] = {}
_LOG_LOCK = threading.Lock()
_LOG_MAXLINES = 2000

_RUNNING: dict[str, tuple[subprocess.Popen, dict, str]] = {}
_RUNNING_LOCK = threading.Lock()
_CANCELLED: set[str] = set()


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
                    "applied", "reverted", "reverted_gate_blocked", "manual_revert_required",
                    "excepted",
                ):
                    return
                fail_body = {
                    "status": "failed",
                    result_col: {
                        "error": f"Process exited with code {proc.returncode}",
                        "log_tail": error_summary,
                    },
                }
                # pending_applies has no completed_at — PostgREST 400s the
                # whole PATCH if we send it, leaving the row stuck in claim.
                if table != "pending_applies":
                    fail_body["completed_at"] = datetime.now(timezone.utc).isoformat()
                requests.patch(
                    f"{url_base}/rest/v1/{table}?id=eq.{run_id}",
                    headers=headers,
                    json=fail_body,
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
