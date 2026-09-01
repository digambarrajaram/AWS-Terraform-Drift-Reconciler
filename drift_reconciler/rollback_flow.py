"""Rollback preview and execute flows."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import github_integration as gi
from terraform_errors import humanize_rollback_error, _strip_ansi
from terraform_ops import _terraform_sub_env_for_scope

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


def _fetch_live_state(tf_dir: str, resource_id: str, fields: list[str], env: dict | None = None) -> tuple[str, dict[str, str]]:
    """Run terraform plan in *tf_dir* and extract live field values for
    *resource_id* from the plan JSON.  Returns (outcome, live_values)
    where outcome is ``"present"``, ``"no_diff"``, or ``"not_found"``."""
    try:
        plan_result = subprocess.run(
            ["terraform", "plan", "-no-color", "-out=tfplan", "-input=false", "-lock-timeout=30s"],
            cwd=tf_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if plan_result.returncode != 0:
            raise RuntimeError(f"terraform plan failed: {_strip_ansi(plan_result.stderr)[:300]}")
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

        # Resolve the AWS subprocess env once — every baseline in this run
        # targets the same scope.
        sub_env = _terraform_sub_env_for_scope(scope)

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
            try:
                _report_rollback_stage(run_id, "fetching_live_state")
                outcome, live_values = _fetch_live_state(
                    tf_dir, resource_id, fields, env=sub_env,
                )
                print(f"  [rollback-preview] RESULT {resource_id}: outcome={outcome}")
            except Exception as exc:
                import traceback
                print(f"  [rollback-preview] UNEXPECTED EXCEPTION for {resource_id}: {exc}")
                traceback.print_exc()
                diff.append({
                    "resource_id": resource_id,
                    "field": "*",
                    "original": "(baseline loaded)",
                    "fixed": "(baseline loaded)",
                    "current_live": f"ERROR: {exc}",
                })
                continue

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

    # Resolve the AWS subprocess env once — every baseline in this run
    # targets the same scope.
    sub_env = _terraform_sub_env_for_scope(account_label)

    print(f"\n--- Rollback checkpoint 1: {len(baselines)} resource(s) in PR #{pr_number} ---\n")

    rollback_ready: list[dict] = []
    for baseline in baselines:
        resource_id = baseline["resource_id"]
        original_changes = baseline["changes"]
        rel_path = baseline.get("file_path", "")
        file_path = gi.resolve_repo_relative_path(tf_dir, rel_path)
        if not file_path or not os.path.isfile(file_path):
            print(f"  ⚠ {resource_id}: source file not found — {file_path}")
            continue

        # Swap before↔after to produce the reverse patch.
        reversed_changes: dict[str, dict] = {}
        for field, vals in original_changes.items():
            reversed_changes[field] = {"before": vals["after"], "after": vals["before"]}

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

        # Freshness check — run terraform plan and extract live values.
        _report_rollback_stage(run_id, "fetching_live_state")
        fields = list(original_changes.keys())
        try:
            outcome, live_values = _fetch_live_state(
                tf_dir, resource_id, fields, env=sub_env,
            )
        except RuntimeError as exc:
            print(f"  ✗ {resource_id}: {exc}")
            continue

        if outcome == "not_found":
            print(f"  ⏭  {resource_id}: not found in plan — may have been deleted externally")
            continue

        if outcome == "no_diff":
            print(f"  ✓ {resource_id}: already matches rollback target — nothing to do")
            continue

        # outcome == "present" — check staleness.
        stale_fields = []
        for field in fields:
            expected = reversed_changes[field]["after"]  # the original "before" value
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
            is_rollback=True,
            rolled_back_from_pr=pr_number,
        )
        if pr and run_id:
            from datetime import datetime as dt, timezone
            from rollback_runs import update_rollback_run
            update_rollback_run(
                run_id,
                status="complete",
                completed_at=dt.now(timezone.utc).isoformat(),
                result={"pr_url": pr.html_url},
                rollback_pr_url=pr.html_url,
            )

    print("\nRollback PR(s) created. Review and merge to revert the original fix.")


