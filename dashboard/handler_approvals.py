"""HTTP handler mixin: ApprovalsMixin."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests

from dashboard.env import _configure_aws_env, _get_valid_scopes, _tf_dir_for
from dashboard.exceptions_policy import auto_add_exceptions_on_merge
from dashboard.paths import _REPO_ROOT
from dashboard.process_runner import _spawn_with_capture

class ApprovalsMixin:
    def _serve_pending_applies(self):
        """GET /api/pending-applies?status=awaiting_approval&scope=scope-a

        Lists pending_applies rows.  *status* defaults to
        ``awaiting_approval``; pass ``status=all`` for every row.
        Optional *scope* filter."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        status = params.get("status", ["awaiting_approval"])[0]
        scope = params.get("scope", [None])[0]
        if not scope or scope not in _get_valid_scopes():
            self._json_error(400, "A valid scope is required")
            return

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
        requested_scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if requested_scope not in _get_valid_scopes():
            self._json_error(400, "A valid scope is required")
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        try:
            resp = requests.get(
                f"{url}/rest/v1/pending_applies"
                f"?select=pr_number,scope&id=eq.{pending_id}"
                f"&scope=eq.{requested_scope}&limit=1",
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
        if decision not in ("approved", "rejected", "excepted"):
            self._json_error(400, "decision must be 'approved', 'rejected', or 'excepted'.")
            return
        approved_by = (self.headers.get("X-Operator-Id") or body.get("approved_by") or "").strip()
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
        # 409s with a clear "Already handled" message, so the GitHub
        # merge/close below can only ever run once.
        from drift_reconciler.pending_applies import claim_decision
        claim = claim_decision(pending_id, decision, approved_by)
        if not claim.get("ok"):
            extra = {k: v for k, v in claim.items()
                     if k not in ("ok", "http_status", "error")}
            self._json_error(claim.get("http_status", 409),
                             claim.get("error", "Decision failed."),
                             **extra)
            return

        row = claim["row"]
        pr_number = row.get("pr_number")
        scope = row.get("scope")
        if not pr_number or not scope:
            try:
                rollback_resp = requests.patch(
                    f"{table_url}?id=eq.{pending_id}&status=eq.{decision}",
                    headers=headers,
                    json={"status": "awaiting_approval", "approved_by": None, "approved_at": None},
                    timeout=10,
                )
                if rollback_resp.status_code >= 300:
                    print(f"  ⚠ claim rollback failed ({rollback_resp.status_code}) for malformed row "
                          f"{pending_id}: {rollback_resp.text[:200]}", file=sys.stderr)
            except requests.RequestException as exc:
                print(f"  ⚠ claim rollback failed for malformed row {pending_id}: {exc}",
                      file=sys.stderr)
            self._json_error(409, "Pending apply row is missing pr_number or scope.")
            return

        # Handler-entry trace: pr_number + pr_type as read from the DB
        # row (the claim PATCH returns representation), so every merge
        # run is auditable end to end.
        print(f"  [approve] decision handler: decision={decision} "
              f"pr={pr_number} scope={scope} pr_type={row.get('pr_type')}",
              file=sys.stderr)

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
            # Manual-review security PRs have no .tf patch — merging only
            # added exceptions before, which is Except's job.  Block Approve.
            if row.get("pr_type") == "security_only" and bool(row.get("review_only")):
                _rollback("approve blocked for review_only security PR")
                self._json_error(
                    400,
                    "Manual-review security PRs cannot be merged — use Except "
                    "to suppress the finding, or Reject to resurface next scan.",
                )
                return
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
            merge_info = {"merged_at": datetime.now(timezone.utc).isoformat()}
            if merge_sha:
                merge_info["merge_commit_sha"] = merge_sha
            try:
                merge_info_resp = requests.patch(
                    f"{table_url}?pr_number=eq.{pr_number}&scope=eq.{scope}",
                    headers=headers, json=merge_info, timeout=10,
                )
                if merge_info_resp.status_code >= 300:
                    self._json_error(502, "PR merged, but merge metadata could not be recorded; apply was not started.")
                    return
            except requests.RequestException as exc:
                print(f"  ⚠ pending row merge-info update failed: {exc}", file=sys.stderr)
                self._json_error(502, "PR merged, but merge metadata could not be recorded; apply was not started.")
                return

            # Policy: unmanaged merge auto-adds exceptions.  Real-fix
            # security applies the .tf patch — no exception.  review_only
            # security is blocked above (use Except).
            _pr_type = row.get("pr_type")
            if _pr_type == "security_only":
                print(f"  [approve] skipping auto_add_exceptions_on_merge — "
                      f"real-fix security PR #{pr_number}", file=sys.stderr)
            else:
                print(f"  [approve] auto_add_exceptions_on_merge reached — "
                      f"pr={pr_number} pr_type={_pr_type}",
                      file=sys.stderr)
                try:
                    auto_add_exceptions_on_merge(
                        pr_number, scope, _pr_type, approved_by=approved_by,
                    )
                    print("  [approve] auto_add_exceptions_on_merge returned "
                          "without exception", file=sys.stderr)
                except Exception as exc:
                    print(f"  [approve] auto_add_exceptions_on_merge raised: {exc}",
                          file=sys.stderr)

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

        elif decision == "excepted":
            # Any security_only PR: close without merging, write exception
            # rows from fixes_jsonb.  Covers real-fix (suppress over patch)
            # and review_only (no patch — Except is the only suppress path).
            if row.get("pr_type") != "security_only":
                _rollback("excepted is only valid for security_only PRs")
                self._json_error(
                    400,
                    "Except is only available for security PRs.",
                )
                return
            fixes = row.get("fixes_jsonb") or []
            if not fixes:
                # Claim representation can omit jsonb — re-read explicitly.
                try:
                    fres = requests.get(
                        f"{table_url}?select=fixes_jsonb&id=eq.{pending_id}&limit=1",
                        headers=headers, timeout=10,
                    )
                    if fres.status_code == 200 and fres.json():
                        fixes = fres.json()[0].get("fixes_jsonb") or []
                except requests.RequestException:
                    fixes = []
            if not fixes:
                _rollback("no fixes_jsonb on pending row — cannot Except")
                self._json_error(
                    409,
                    "Cannot Except: this security PR has no recorded "
                    "(resource, rule_id) pairs. Re-run the security scan to "
                    "recreate the PR, then try Except again.",
                )
                return
            try:
                from drift_reconciler.github_integration import close_pr
                close_pr(scope, pr_number)
            except RuntimeError as exc:
                _rollback(f"close failed: {exc}")
                self._json_error(409, f"GitHub close failed: {exc}")
                return
            try:
                n = auto_add_exceptions_on_merge(
                    pr_number, scope, "security_only", approved_by=approved_by,
                    reason=f"Excepted via dashboard on security PR #{pr_number}",
                    strict=True,
                )
                print(f"  [except] auto_add inserted {n} exception row(s) "
                      f"for PR #{pr_number}", file=sys.stderr)
                if n == 0:
                    print(f"  ⚠ [except] PR #{pr_number} closed but 0 exceptions "
                          f"were written — check fixes_jsonb / rule_id column",
                          file=sys.stderr)
            except Exception as exc:
                print(f"  [except] auto_add_exceptions_on_merge raised: {exc}",
                      file=sys.stderr)
                try:
                    requests.patch(
                        f"{table_url}?id=eq.{pending_id}", headers=headers,
                        json={"status": "failed", "result": {"error": str(exc), "excepted": False}},
                        timeout=10,
                    )
                except requests.RequestException as patch_exc:
                    print(f"  [except] failed status update failed: {patch_exc}", file=sys.stderr)
                self._json_error(502, f"Exception could not be recorded: {exc}")
                return
            try:
                from drift_reconciler import drift_history as _dh
                _dh.resolve_entry(
                    pr_number, scope,
                    f"Excepted via dashboard — security finding suppressed "
                    f"without merging PR #{pr_number}",
                )
            except Exception as exc:
                print(f"  [except] drift_history resolve failed: {exc}",
                      file=sys.stderr)
            try:
                requests.patch(
                    f"{table_url}?id=eq.{pending_id}",
                    headers=headers,
                    json={
                        "status": "excepted",
                        "result": {
                            "excepted": True,
                            "exceptions_added": n,
                            "message": "PR closed without merge; security exception written",
                        },
                    },
                    timeout=10,
                )
            except requests.RequestException as exc:
                print(f"  [except] pending status update failed: {exc}",
                      file=sys.stderr)
            data = json.dumps({
                "ok": True, "apply_started": False, "excepted": True,
                "exceptions_added": n,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return


        # ── Spawn the apply/revert job asynchronously ─────────────────
        # Approve → --apply-pr against post-merge main.
        # Reject  → --revert-pr against pre-drift main (PR never merged),
        #           so the apply reverts AWS to match code.
        # (row/pr_number/scope come from the claim above.)
        if pr_number and scope and decision != "excepted":
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
                sys.executable,
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
