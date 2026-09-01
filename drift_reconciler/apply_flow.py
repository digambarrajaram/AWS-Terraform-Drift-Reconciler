"""PR apply / revert flows."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import github_integration as gi
import drift_reconciler.drift_history as drift_history
from drift_reconciler.environment_credentials import _resolve_env_credentials
from terraform_errors import humanize_terraform_error, _strip_ansi

def _revert_on_gate_failure(
    tf_dir: str, pr_number: int, scope: str, reason: str, env_dict: dict,
) -> dict:
    """Attempt to revert the merged drift-fix commit after a gate failure.

    Mirrors drift-reconciler.yml's recovery step: git config → revert -m 1
    → push.  The merge_commit_sha is read from the pending_applies row.

    Returns ``{"status": ..., "result": {...}}`` — never raises: a failed
    revert gets the distinct 'manual_revert_required' status instead of
    collapsing into the generic 'failed' bucket."""
    import requests as _requests

    def _outcome(status: str, result: dict) -> dict:
        return {"status": status, "result": result}

    # Fetch merge_commit_sha from the approved pending_applies row.
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    sha = ""
    if url and key:
        try:
            resp = _requests.get(
                f"{url}/rest/v1/pending_applies"
                f"?select=merge_commit_sha"
                f"&pr_number=eq.{pr_number}&scope=eq.{scope}&status=eq.approved&limit=1",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                sha = (resp.json()[0].get("merge_commit_sha") or "").strip()
        except _requests.RequestException as exc:
            return _outcome("manual_revert_required", {
                "reason": reason,
                "reverted": False,
                "error": f"could not fetch merge_commit_sha: {exc}",
                "message": "Automatic revert failed — could not read the merge commit SHA. Manual revert required.",
            })

    if not sha:
        return _outcome("manual_revert_required", {
            "reason": reason,
            "reverted": False,
            "error": "pending_applies row has no merge_commit_sha",
            "message": "Automatic revert failed — no merge commit SHA recorded. Manual revert required.",
        })

    branch = (env_dict.get("repo_branch") or "main").strip() or "main"

    def _git(args: list[str], timeout: int = 120) -> None:
        # Git config + revert + push must run inside tf_dir so they hit
        # the clone's repo (origin URL carries the token when git_auth_type='token').
        subprocess.run(
            ["git"] + args,
            cwd=tf_dir,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            check=True,
        )

    try:
        print(f"[apply] Reverting merge commit {sha} on {branch} …")
        _git(["config", "user.name", "drift-reconciler"])
        _git(["config", "user.email", "drift-reconciler@noreply"])
        _git(["revert", "--no-edit", "-m", "1", sha])
        _git(["push", "origin", branch], timeout=300)
        print("[apply] ✓ Revert pushed — code and live state are consistent again.")
        return _outcome("reverted_gate_blocked", {
            "reason": reason,
            "reverted": True,
            "commit": sha,
        })
    except subprocess.CalledProcessError as exc:
        print(f"[apply] ✗ Automatic revert failed: {exc.stderr[:300] if hasattr(exc, 'stderr') else exc}")
        return _outcome("manual_revert_required", {
            "reason": reason,
            "reverted": False,
            "error": f"{exc}",
            "message": (
                f"Automatic revert failed — this environment's git credentials may "
                f"be read-only. Manual revert of commit {sha} required."
            ),
        })
    except subprocess.TimeoutExpired as exc:
        print(f"[apply] ✗ Automatic revert timed out: {exc}")
        return _outcome("manual_revert_required", {
            "reason": reason,
            "reverted": False,
            "error": f"timed out: {exc}",
            "message": f"Automatic revert timed out. Manual revert of commit {sha} required.",
        })


_FILE_ONLY_PR_TYPES = ("unmanaged", "security_only")


def _pr_requires_terraform(pr_number: int, scope: str) -> bool:
    """Dispatcher for the apply/reject flows: True when this PR goes
    through the terraform gate/apply path.

    File-only PRs (pr_type ``unmanaged`` — an unmanaged-resource report —
    or ``security_only`` — a security patch to the .tf file) ARE the fix
    in the PR itself: no init/plan/apply/revert, no drift gate.  All other
    types (drift/fix, batch, rollback) keep the full gate/apply/revert
    path.  Falls back to the terraform path when the pr_type can't be
    read (DB down) — today's behavior."""
    from drift_reconciler.drift_history import get_pr_type
    return get_pr_type(pr_number, scope) not in _FILE_ONLY_PR_TYPES


def _run_apply(tf_dir: str, pr_number: int, scope: str, run_id: str | None = None, is_revert: bool = False) -> None:
    import agent as _ag
    """Apply an approved drift fix for *pr_number* in *scope*, or revert
    AWS for a REJECTED fix when *is_revert* is True.

    Mirrors the GitHub Actions ACCEPT path (terraform init + apply) but
    runs on the server: builds an AWS session via ``get_aws_session``
    (role/keys/profile per the environment row) and injects the
    credentials into the terraform subprocess env.

    Writes the pending_applies row to 'applied' on success and 'failed'
    on error — the write happens in BOTH branches, so an exception can't
    leave the row stuck on 'approved' forever.  For reverts (PR never
    merged, main still pre-drift) the drift_events rows are marked
    'reverted' instead of 'resolved' on success."""
    import requests as _requests
    from drift_reconciler.pending_applies import update_pending_apply
    from datetime import datetime as _dt, timezone as _tz

    def _finish(status: str, result: dict) -> None:
        update_pending_apply(
            pr_number,
            scope,
            status=status,
            applied_at=_dt.now(_tz.utc).isoformat(),
            result=result,
        )

    try:
        # File-only PRs (unmanaged / security) skip terraform entirely —
        # the PR itself is the fix.  The GitHub side (merge on approve,
        # close on reject) already happened in serve.py; just finalize
        # the DB rows here, no init/plan/apply/revert, no drift gate.
        if not _ag._pr_requires_terraform(pr_number, scope):
            import drift_history as _dh
            if is_revert:
                _dh.mark_reverted(
                    pr_number, scope, status="rejected",
                    resolution=("PR rejected — file-only PR "
                                "(unmanaged/security), no AWS change needed"),
                )
            else:
                _dh.resolve_entry(
                    pr_number, scope,
                    "PR merged — file-only change applied (no terraform action)",
                )
            # Revert writes 'reverted' — NOT 'rejected'.  The decision
            # handler already claimed this row with status='rejected' when
            # the job started; writing it again here leaves the row looking
            # claim-pending forever, so the dashboard log poller (which
            # keys "done" off terminal statuses) never stops polling.
            _finish(
                "reverted" if is_revert else "applied",
                {"output": "file-only PR — no terraform action", "terraform": False},
            )
            print(f"--- PR #{pr_number} ({scope}) is a file-only "
                  f"{'revert' if is_revert else 'apply'} — no terraform action ---")
            return

        # Fetch the environment row for the AWS session (same pattern as
        # unmanaged_scan_node).
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        env_dict = {}
        if url and key:
            resp = _requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{scope}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        if not env_dict:
            raise RuntimeError(f"No environment found for slug '{scope}' — check the environments table.")

        sub_env = _resolve_env_credentials(env_dict)

        mode_label = "reverting drift" if is_revert else "applying accepted drift"
        print(f"\n--- {mode_label} for PR #{pr_number} ({scope}) ---")
        
        # Build backend_config dict from environment row
        backend_config = {}
        if env_dict:
            if env_dict.get("tf_state_bucket"):
                backend_config["bucket"] = env_dict["tf_state_bucket"]
            if env_dict.get("tf_lock_table"):
                backend_config["dynamodb_table"] = env_dict["tf_lock_table"]
            if env_dict.get("region"):
                backend_config["region"] = env_dict["region"]
        
        print(f"[apply] ({mode_label}) terraform init in {tf_dir} …")
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        if backend_config:
            for key, value in backend_config.items():
                if value:
                    cmd.append(f"-backend-config={key}={value}")
        
        # Cold-init detection: no cached providers yet → first-ever init for
        # this clone (new environment) must download providers; 900s for
        # that, 300s otherwise (warm init is seconds with a provider cache).
        cold_init = not os.path.isdir(os.path.join(tf_dir, ".terraform", "providers"))
        try:
            init = subprocess.run(
                cmd,
                cwd=tf_dir, env=sub_env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=900 if cold_init else 300,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "terraform init timed out — likely cold provider download "
                "for a new environment; retry or increase the init timeout"
            )
        if init.returncode != 0:
            raise RuntimeError(f"terraform init failed:\n{_strip_ansi(init.stderr)[:800]}")

        print(f"[apply] ({mode_label}) terraform plan …")
        plan = subprocess.run(
            ["terraform", "plan", "-no-color", "-out=tfplan", "-input=false", "-lock-timeout=30s"],
            cwd=tf_dir, env=sub_env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
        if plan.returncode != 0:
            raise RuntimeError(f"terraform plan failed:\n{_strip_ansi(plan.stderr)[:800]}")

        # ── Safety gates (mirror drift-reconciler.yml ACCEPT path) ────
        gate_failure: str | None = None

        # The plan JSON is the live drift snapshot at gate-check time —
        # parse it once and share it between Gate A (live unresolved-drift
        # check) and Gate B (rollback freshness).
        show_result = subprocess.run(
            ["terraform", "show", "-no-color", "-json", "tfplan"],
            cwd=tf_dir, env=sub_env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        if show_result.returncode != 0:
            raise RuntimeError(f"terraform show failed:\n{_strip_ansi(show_result.stderr)[:800]}")
        try:
            plan_json = json.loads(show_result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError("terraform show -json produced unparseable output")

        # Gate A: pre-apply drift gate.  Only meaningful for normal applies
        # — a revert's whole purpose is fixing existing drift, so open rows
        # are expected and must not block it (Gate B below is the
        # revert-specific check: the plan must match the stored baseline).
        # Excludes this PR's own rows (they stay 'open' until the
        # post-apply resolve step — including them would fail every apply).
        # Open rows are detection-time state, so re-verify them against
        # the live plan: a resource fixed in AWS since the scan no longer
        # appears in the plan and must not block this apply.
        from drift_reconciler.drift_history import get_open_resources, load_baselines
        from rollback_check import _extract_field_values, live_drift_rows
        if not is_revert:
            open_rows = get_open_resources(scope, except_pr_number=pr_number)
            if open_rows:
                live_drift = live_drift_rows(open_rows, plan_json)
                if live_drift:
                    gate_failure = (
                        "pre_apply_check: unresolved drift exists in scope — "
                        + ", ".join(str(r.get("resource_id")) for r in live_drift[:5])
                    )

        # Gate B: rollback freshness gate — compare live values from the
        # plan JSON against the stored baseline (changes_jsonb) for this
        # PR.  Shape confirmed from rollback_check.py's comparison loop
        # and drift_history.load_baselines().
        if not gate_failure:
            baselines = load_baselines(pr_number, scope)
            if not baselines:
                # Fail closed: load_baselines returns [] when the PR has no
                # changes_jsonb rows, when every row's changes_jsonb is
                # NULL, or when the baseline fetch itself failed — in all
                # three the plan can't be verified against recorded state,
                # so the revert must not apply unverified.  (Previously
                # `if baselines:` silently skipped the entire gate.)
                gate_failure = (
                    "rollback_check: no usable baseline for this PR — "
                    "cannot verify revert safety"
                )
            else:
                for baseline in baselines:
                    resource_id = baseline["resource_id"]
                    changes = baseline.get("changes") or {}
                    fields = list(changes.keys())
                    if not fields:
                        # Fail closed too: a baseline with no recorded field
                        # changes verifies nothing — don't skip the check.
                        gate_failure = (
                            f"rollback_check: baseline for {resource_id} has "
                            f"no recorded field changes — cannot verify revert safety"
                        )
                        break
                    outcome, live_values = _extract_field_values(plan_json, resource_id, fields)
                    if outcome == "not_found":
                        # Fail closed: the resource this baseline was recorded
                        # for isn't in the plan at all (absent from state, or
                        # being created) — there are no live values to verify
                        # against, so its baseline fields went unchecked.
                        gate_failure = (
                            f"rollback_check: baseline for {resource_id} not "
                            f"found in current plan — cannot verify revert safety"
                        )
                        break
                    if outcome == "no_diff":
                        # Legitimate skip: the resource IS in the plan with
                        # live state already equal to the rollback target —
                        # nothing to revert, nothing unverified.  Mirrors
                        # rollback_check.py's "already matches rollback
                        # target — nothing to apply" no-op.
                        continue
                    for field in fields:
                        # Revert direction: live may legitimately be either
                        # baseline state — the fix-applied state (baseline
                        # "after" for a fix PR; "before" for a rollback PR)
                        # or the pre-fix state (a no-op revert plan, caught
                        # above as no_diff, but harmless to allow).  Any
                        # third value means live changed since capture —
                        # the plan's from-state is stale, fail closed.
                        # Accept direction: the plan's from-state is the
                        # baseline "before" — unchanged.
                        expected = {str(changes[field].get("before", ""))}
                        if is_revert:
                            expected.add(str(changes[field].get("after", "")))
                        actual = live_values.get(field, "<missing>")
                        if actual not in expected:
                            gate_failure = (
                                f"rollback_check: stale field {resource_id}.{field} "
                                f"(expected={'|'.join(sorted(expected))[:60]} "
                                f"actual={actual[:60]})"
                            )
                            break
                    if gate_failure:
                        break

        if gate_failure:
            print(f"[apply] ⛔ Gate failed: {gate_failure}")
            if is_revert:
                # Reject path: the PR was never merged, so there is no
                # merge commit to un-merge — git revert is impossible and
                # pointless.  Mark manual action required with the real
                # reason (the gate), not the misleading "no merge SHA" error.
                _finish("manual_revert_required", {
                    "reason": gate_failure,
                    "reverted": False,
                    "error": "safety gate blocked the revert apply",
                    "message": (
                        f"Revert apply blocked by safety gate ({gate_failure}). "
                        f"The PR was closed without merging — manual AWS revert "
                        f"to match main's code is required."
                    ),
                })
                # The decision is final — close out this PR's drift_events
                # rows so future scans/approvals don't re-block on them
                # (the cascade that kept rejecting every later PR).  The
                # AWS resource was NOT reverted, so the status must say
                # manual_revert_required — 'reverted' would be a lie.
                import drift_history as _dh
                _dh.mark_reverted(
                    pr_number, scope,
                    status="manual_revert_required",
                    resolution=(
                        f"Revert blocked by safety gate ({gate_failure}) — "
                        f"AWS NOT reverted; manual AWS revert required"
                    ),
                )
                return
            self_status = _revert_on_gate_failure(
                tf_dir, pr_number, scope, gate_failure, env_dict,
            )
            _finish(self_status["status"], self_status["result"])
            # Close out this PR's drift_events rows too — the apply was
            # blocked, so they must not stay open (the revert branch above
            # already marks its own rows).  manual_revert_required is the
            # honest label: the gate blocked the apply, so AWS vs code
            # must be verified by hand regardless of the auto-revert.
            import drift_history as _dh
            _dh.mark_reverted(
                pr_number, scope, status="manual_revert_required",
                resolution=(
                    f"Apply blocked by safety gate ({gate_failure}) — "
                    f"auto-revert status: {self_status['status']}; "
                    f"manual verification required"
                ),
            )
            return

        print(f"[apply] ({mode_label}) terraform apply -auto-approve …")
        apply_result = subprocess.run(
            ["terraform", "apply", "-no-color", "-auto-approve", "-input=false", "-lock-timeout=30s", "tfplan"],
            cwd=tf_dir, env=sub_env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
        if apply_result.returncode != 0:
            raise RuntimeError(f"terraform apply failed:\n{_strip_ansi(apply_result.stderr)[:800]}")

        tail = _strip_ansi(apply_result.stdout)[-2000:]
        print(f"[apply] ✓ {mode_label} complete for PR #{pr_number} ({scope})")
        _finish("applied", {"output": tail})

        # Update drift_events to match the outcome.  Approve → resolved
        # (code now matches live AWS).  Revert → reverted (AWS reverted
        # to match pre-drift code; PR was never merged).
        if is_revert:
            import drift_history as _dh
            _dh.mark_reverted(pr_number, scope)
        else:
            import drift_history as _dh
            _dh.resolve_entry(pr_number, scope, "PR merged via dashboard — code updated to match live AWS state")
    except subprocess.TimeoutExpired as exc:
        print(f"[apply] timed out: {exc}")
        _finish("failed", {"error": f"timed out: {exc}"})
        # The job is terminal — don't leave this PR's drift_events rows
        # open waiting for a write that will never come.
        import drift_history as _dh
        _dh.mark_reverted(
            pr_number, scope, status="manual_revert_required",
            resolution=("Apply timed out — verify AWS vs code; "
                        "manual action may be required"),
        )
    except Exception as exc:
        print(f"[apply] failed: {exc}")
        _finish("failed", {"error": humanize_terraform_error(str(exc))})
        import drift_history as _dh
        _dh.mark_reverted(
            pr_number, scope, status="manual_revert_required",
            resolution=f"Apply failed: {exc} — verify AWS vs code; manual action may be required",
        )


