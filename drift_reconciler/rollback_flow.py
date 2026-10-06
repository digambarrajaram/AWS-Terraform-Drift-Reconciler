"""Rollback preview and execute flows."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import github_integration as gi
from drift_baseline import (
    DELETED_EXTERNALLY_FIELD,
    TF_FILE_FIELD,
    deleted_externally_baseline,
    is_deleted_externally_baseline,
    is_tf_file_baseline,
    tf_file_before,
    tf_file_baseline,
    verify_deleted_externally_plan,
)
from terraform_errors import humanize_rollback_error, _strip_ansi
from terraform_ops import (
    _ensure_terraform_init,
    _strip_hardcoded_aws_profile,
    _terraform_plan_needs_reinit,
    _terraform_sub_env_for_scope,
)


def _prepare_terraform_workspace(tf_dir: str, scope: str) -> tuple[dict, dict]:
    """Resolve scope credentials and ensure terraform init (incl. backend)."""
    from drift_reconciler.scope_resolution import (
        backend_config_from_environment,
        fetch_environment_row,
    )

    sub_env = _terraform_sub_env_for_scope(scope, tf_dir=tf_dir)
    _strip_hardcoded_aws_profile(tf_dir)
    env_dict = fetch_environment_row(scope)
    backend_config = backend_config_from_environment(env_dict)
    init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        raise RuntimeError(init_error.strip())
    return sub_env, backend_config


def _report_rollback_stage(run_id: str | None, stage_name: str) -> None:
    """Update rollback_runs.current_stage.  No-ops when run_id is None."""
    if run_id is None:
        return
    from rollback_runs import update_rollback_run
    update_rollback_run(run_id, current_stage=stage_name)


def _load_rollback_baselines(pr_number: int, scope: str) -> list[dict]:
    """Return rollback baselines for *pr_number* from Supabase."""
    import drift_history
    return drift_history.load_baselines(pr_number, scope)


def _restore_tf_file_from_git(tf_dir: str, rel_path: str, ref: str = "HEAD") -> bool:
    """Overwrite *rel_path* in the clone with the version at *ref* (rollback of
    deleted_externally fixes that removed the resource block)."""
    if not rel_path:
        return False
    show = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if show.returncode != 0:
        return False
    abs_path = gi.resolve_repo_relative_path(tf_dir, rel_path)
    if not abs_path:
        return False
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(show.stdout)
    return True


def _fetch_live_state(
    tf_dir: str,
    resource_id: str,
    fields: list[str],
    env: dict | None = None,
    backend_config: dict | None = None,
) -> tuple[str, dict[str, str]]:
    """Run terraform plan in *tf_dir* and extract live field values for
    *resource_id* from the plan JSON.  Returns (outcome, live_values)
    where outcome is ``"present"``, ``"no_diff"``, or ``"not_found"``."""
    plan_cmd = [
        "terraform", "plan", "-no-color", "-out=tfplan", "-input=false", "-lock-timeout=30s",
    ]
    forced_reinit = False
    try:
        while True:
            plan_result = subprocess.run(
                plan_cmd,
                cwd=tf_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            if plan_result.returncode == 0:
                break
            stderr = _strip_ansi(plan_result.stderr)
            # Backend HCL can change even when env row bucket/region match the
            # init cache — force re-init once, then retry plan.
            if not forced_reinit and _terraform_plan_needs_reinit(stderr):
                forced_reinit = True
                print("  [rollback] plan needs backend re-init — running terraform init -reconfigure")
                init_error = _ensure_terraform_init(
                    tf_dir, env=env, backend_config=backend_config or {}, force=True,
                )
                if init_error:
                    raise RuntimeError(init_error.strip())
                continue
            raise RuntimeError(f"terraform plan failed: {stderr[:300]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("terraform plan timed out after 120s — check AWS credentials and state lock")

    show_result = subprocess.run(
        ["terraform", "show", "-no-color", "-json", "tfplan"],
        cwd=tf_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        plan_json = json.loads(show_result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Failed to parse terraform plan JSON")

    return gi._extract_field_values(plan_json, resource_id, fields)


def _run_rollback_preview(tf_dir: str, pr_number: int, scope: str, run_id: str) -> None:
    """Dry-run rollback: compare baselines against live AWS without
    patching any files or creating a PR.  Results are written to
    rollback_runs in Supabase."""
    from datetime import datetime as dt, timezone
    from rollback_runs import update_rollback_run

    try:
        _report_rollback_stage(run_id, "loading_baseline")
        baselines = _load_rollback_baselines(pr_number, scope)
        if not baselines:
            raise RuntimeError(f"No baselines found for PR #{pr_number} ({scope})")

        # Resolve credentials and init backend once for all baselines in this run.
        sub_env, backend_config = _prepare_terraform_workspace(tf_dir, scope)

        diff: list[dict] = []

        for baseline in baselines:
            resource_id = baseline["resource_id"]
            original_changes = baseline["changes"]
            rel_path = baseline.get("file_path", "")
            file_path = gi.resolve_repo_relative_path(tf_dir, rel_path)
            if not file_path or not os.path.isfile(file_path):
                print(f"  [rollback-preview] SKIP {resource_id}: file not found — {file_path}")
                diff.append({
                    "resource_id": resource_id,
                    "field": "*",
                    "original": "(baseline loaded)",
                    "fixed": "(baseline loaded)",
                    "current_live": "SKIPPED: source .tf file not found on disk",
                })
                continue

            if is_deleted_externally_baseline(original_changes):
                print(f"  [rollback-preview] CHECK {resource_id}: deleted_externally baseline")
                # Drive plan through _fetch_live_state so backend re-init retry applies.
                _fetch_live_state(
                    tf_dir, resource_id, [], env=sub_env, backend_config=backend_config,
                )
                show_result = subprocess.run(
                    ["terraform", "show", "-no-color", "-json", "tfplan"],
                    cwd=tf_dir,
                    env=sub_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                plan_json = json.loads(show_result.stdout)
                err = verify_deleted_externally_plan(
                    plan_json, resource_id, is_revert=True,
                )
                diff.append({
                    "resource_id": resource_id,
                    "field": DELETED_EXTERNALLY_FIELD,
                    "original": "present_in_aws",
                    "fixed": "missing_in_aws",
                    "current_live": err or "create planned — OK",
                })
                continue

            fields = list(original_changes.keys())
            if not fields:
                print(f"  [rollback-preview] SKIP {resource_id}: no fields in baseline changes")
                diff.append({
                    "resource_id": resource_id,
                    "field": "*",
                    "original": "(empty baseline)",
                    "fixed": "(empty baseline)",
                    "current_live": "SKIPPED: baseline changes_jsonb has no fields",
                })
                continue

            print(f"  [rollback-preview] CHECK {resource_id}: {len(fields)} field(s) — {list(fields)[:5]}...")
            _report_rollback_stage(run_id, "fetching_live_state")
            # Plan failures must fail the run — do not mark preview complete
            # with ERROR rows (UI used to treat those as "stale" and allow Execute).
            outcome, live_values = _fetch_live_state(
                tf_dir, resource_id, fields, env=sub_env, backend_config=backend_config,
            )
            print(f"  [rollback-preview] RESULT {resource_id}: outcome={outcome}")

            if outcome == "not_found":
                continue

            for field in fields:
                original_val = original_changes[field].get("before")
                fixed_val = original_changes[field].get("after")
                current_val = live_values.get(field, "<missing>") if outcome == "present" else fixed_val
                diff.append({
                    "resource_id": resource_id,
                    "field": field,
                    "original": original_val,
                    "fixed": fixed_val,
                    "current_live": current_val,
                })

        update_rollback_run(
            run_id,
            status="complete",
            completed_at=dt.now(timezone.utc).isoformat(),
            result={"diff": diff},
        )
    except Exception as e:
        try:
            update_rollback_run(
                run_id,
                status="failed",
                completed_at=dt.now(timezone.utc).isoformat(),
                result=humanize_rollback_error(str(e)),
            )
        except Exception:
            pass  # let the original exception propagate — don't mask it
        raise


def _run_rollback(tf_dir: str, pr_number: int, run_id: str | None = None) -> None:
    """Checkpoint 1: validate freshness and open a rollback PR for every
    resource in the baseline of *pr_number*.

    Skips the normal drift-detection pipeline — this is a standalone
    rollback flow.  Baselines are loaded from Supabase (no local file
    dependency — works from any machine, no git pull needed)."""
    try:
        _do_run_rollback(tf_dir, pr_number, run_id)
    except Exception as e:
        if run_id:
            from datetime import datetime as dt, timezone
            from rollback_runs import update_rollback_run
            try:
                update_rollback_run(run_id, status="failed", completed_at=dt.now(timezone.utc).isoformat(), result=humanize_rollback_error(str(e)))
            except Exception:
                pass
        raise


def _do_run_rollback(tf_dir: str, pr_number: int, run_id: str | None) -> None:
    """Inner implementation — wrapped by _run_rollback for error handling."""
    import agent as _ag
    account_label = _ag._account_label
    _report_rollback_stage(run_id, "loading_baseline")
    baselines = _load_rollback_baselines(pr_number, account_label)
    if not baselines:
        raise RuntimeError(f"No baselines found in Supabase for PR #{pr_number} ({account_label})")

    sub_env, backend_config = _prepare_terraform_workspace(tf_dir, account_label)

    print(f"\n--- Rollback checkpoint 1: {len(baselines)} resource(s) in PR #{pr_number} ---\n")

    rollback_ready: list[dict] = []
    plan_errors: list[str] = []
    for baseline in baselines:
        resource_id = baseline["resource_id"]
        original_changes = baseline["changes"]
        rel_path = baseline.get("file_path", "")
        file_path = gi.resolve_repo_relative_path(tf_dir, rel_path)
        if not file_path or not os.path.isfile(file_path):
            print(f"  ⚠ {resource_id}: source file not found — {file_path}")
            continue

        if is_deleted_externally_baseline(original_changes):
            print(f"  ↻ {resource_id}: restoring .tf from git (deleted_externally rollback) …")
            rel = gi.to_repo_relative_path(file_path)
            if not _restore_tf_file_from_git(tf_dir, rel):
                print(f"  ✗ {resource_id}: could not restore {rel} from git — skipping")
                continue
            patched = None
            with open(file_path, encoding="utf-8") as fh:
                patched = fh.read()
        elif is_tf_file_baseline(original_changes):
            # Security hardening: restore the pre-fix whole-file content.
            before = tf_file_before(original_changes)
            if before is None:
                print(f"  ✗ {resource_id}: __tf_file__ baseline missing before content — skipping")
                continue
            print(f"  ↻ {resource_id}: restoring pre-security .tf content …")
            _report_rollback_stage(run_id, "patching_file")
            try:
                with open(file_path, encoding="utf-8") as fh:
                    current = fh.read()
                if current == before:
                    print(f"  ✓ {resource_id}: already matches rollback target — nothing to do")
                    continue
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(before)
                patched = before
            except OSError as exc:
                print(f"  ✗ {resource_id}: failed to restore file — {exc}")
                continue
            after_content = (original_changes.get(TF_FILE_FIELD) or {}).get("after") or current
            reversed_changes = tf_file_baseline(after_content, before)
        else:
            # Swap before↔after to produce the reverse patch.
            reversed_changes: dict[str, dict] = {}
            for field, vals in original_changes.items():
                reversed_changes[field] = {"before": vals["after"], "after": vals["before"]}
            reversed_changes = gi.filter_patchable_changes(reversed_changes)

            print(f"  ↻ {resource_id}: reversing {len(reversed_changes)} field(s) …")

            _report_rollback_stage(run_id, "patching_file")
            # Apply the reverse patch to a temp copy.
            patched = gi.apply_changes_to_file(file_path, resource_id, reversed_changes)
            if patched is None:
                print(f"  ✗ {resource_id}: reverse-patch produced no changes — skipping")
                continue

            # Write the patched content back so terraform plan sees it.
            try:
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(patched)
            except OSError as exc:
                print(f"  ✗ {resource_id}: failed to write patched file — {exc}")
                continue

        if is_deleted_externally_baseline(original_changes):
            reversed_changes = deleted_externally_baseline()
            _report_rollback_stage(run_id, "fetching_live_state")
            try:
                _fetch_live_state(
                    tf_dir, resource_id, [], env=sub_env, backend_config=backend_config,
                )
                show_result = subprocess.run(
                    ["terraform", "show", "-no-color", "-json", "tfplan"],
                    cwd=tf_dir,
                    env=sub_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                plan_json = json.loads(show_result.stdout)
                err = verify_deleted_externally_plan(
                    plan_json, resource_id, is_revert=True,
                )
                if err:
                    print(f"  ✗ {resource_id}: {err}")
                    continue
            except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                print(f"  ✗ {resource_id}: {exc}")
                plan_errors.append(str(exc))
                continue
            print(f"  ✓ {resource_id}: freshness confirmed (create planned)")
        elif is_tf_file_baseline(original_changes):
            # Whole-file restore already written; plan/apply of the rollback
            # PR is the infra gate (Gate C on approve).
            print(f"  ✓ {resource_id}: pre-security file content restored for rollback PR")
        else:
            # Freshness check — run terraform plan and extract live values.
            _report_rollback_stage(run_id, "fetching_live_state")
            fields = list(original_changes.keys())
            try:
                outcome, live_values = _fetch_live_state(
                    tf_dir, resource_id, fields, env=sub_env, backend_config=backend_config,
                )
            except RuntimeError as exc:
                print(f"  ✗ {resource_id}: {exc}")
                plan_errors.append(str(exc))
                continue

            if outcome == "not_found":
                print(f"  ⏭  {resource_id}: not found in plan — may have been deleted externally")
                continue

            if outcome == "no_diff":
                print(f"  ✓ {resource_id}: already matches rollback target — nothing to do")
                continue

            # outcome == "present" — check staleness.
            # Live should still be the drifted value captured at fix time
            # (original "after" / reversed "before"). A third value means
            # intervening change since the original fix.
            stale_fields = []
            for field in fields:
                expected = reversed_changes[field]["before"]
                actual = live_values.get(field, "<missing>")
                if actual != expected:
                    stale_fields.append((field, expected, actual))

            if stale_fields:
                print(f"  ⚠ {resource_id}: intervening changes detected since original fix:")
                for field, expected, actual in stale_fields:
                    print(f"      {field}: expected={expected}  actual={actual}")
                print(f"      (checkpoint 2 at apply time will still validate freshness)")
            else:
                print(f"  ✓ {resource_id}: freshness confirmed")

        rollback_ready.append(
            {
                "resource_id": resource_id,
                "file_path": file_path,
                "reversed_changes": reversed_changes,
                "risk_level": "LOW",
                "drift_summary": f"Rollback of PR #{pr_number}: reverting {resource_id} to pre-fix state.",
                "plan_output": json.dumps(
                    {"reversed_changes": {f: {"before": v["before"], "after": v["after"]}
                                          for f, v in reversed_changes.items()}},
                    indent=2,
                ),
            }
        )

    if not rollback_ready:
        if plan_errors:
            # Do not mis-report plan/backend failures as "already matches target".
            raise RuntimeError(plan_errors[0])
        print("\nNo resources passed freshness check — rollback aborted.")
        raise RuntimeError(
            "No resources passed freshness check — live state already "
            "matches rollback target, nothing to revert."
        )

    print(f"\n{len(rollback_ready)} resource(s) passed freshness check — opening rollback PR …")
    _report_rollback_stage(run_id, "creating_pr")
    for rb in rollback_ready:
        # File was already patched on disk for the freshness check —
        # just read it back instead of re-patching (which would double-patch).
        try:
            with open(rb["file_path"], encoding="utf-8") as fh:
                patched_content = fh.read()
        except OSError:
            print(f"  ⚠ {rb['resource_id']}: failed to read patched file — skipping")
            continue
        pr = gi.create_drift_pr(
            resource_id=f"{rb['resource_id']}-rollback",
            pr_title=f"[ROLLBACK] Drift fix: {rb['resource_id']}",
            drift_summary=rb["drift_summary"],
            plan_output=rb["plan_output"],
            file_path=gi.to_repo_relative_path(rb["file_path"]),
            file_content=patched_content,
            risk_level="LOW",
            account_label=account_label,
            # reversed_changes so Approvals apply Gate B can verify freshness
            # against the real TF resource (suffix stripped in create_drift_pr).
            changes=rb["reversed_changes"],
            is_rollback=True,
            rolled_back_from_pr=pr_number,
        )
        if pr is None:
            continue
        # Same Approvals gate as fix/batch/security: PR lands in
        # pending_applies immediately so Accept/Revert applies it.
        from drift_reconciler.pending_applies import create_pending_apply
        create_pending_apply(pr.number, account_label, "rollback")
        if run_id:
            from datetime import datetime as dt, timezone
            from rollback_runs import update_rollback_run
            update_rollback_run(
                run_id,
                status="complete",
                completed_at=dt.now(timezone.utc).isoformat(),
                result={"pr_url": pr.html_url},
                rollback_pr_url=pr.html_url,
            )

    print("\nRollback PR(s) created — review on the Approvals page to apply.")


