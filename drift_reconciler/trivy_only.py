"""Standalone --trivy-only scan path."""
from __future__ import annotations

import difflib
import os
import shutil
import tempfile

import unmanaged_scanner
from trivy_agent import (
    _extract_issues,
    copy_tf_tree,
    State as TrivyState,
)


def _verify_fixes_cleared(
    tmpdir: str,
    all_fixes: list[dict],
    needs_review: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Re-scan after LLM patches; drop fixes that did not clear Trivy.

    Merged-but-ineffective rewrites used to reopen the same AWS-00xx PR
    every scan because the agent trusted the LLM without checking Trivy.
    Those pairs become manual-review items instead.
    """
    # Route through agent so tests that patch agent._run_trivy also cover
    # the post-fix verify pass (same binding as the initial scan).
    import agent as _ag
    raw = _ag._run_trivy(tmpdir)
    if "error" in raw:
        print(f"  ⚠ [trivy-only] post-fix re-scan failed: {raw['error']} — "
              f"keeping {len(all_fixes)} unverified fix(es)")
        return all_fixes, needs_review

    remaining = {
        (i.get("resource") or "", i.get("rule_id") or "")
        for i in _extract_issues(raw, tmpdir)
        if i.get("rule_id")
    }
    remaining_rules = {rid for _r, rid in remaining}

    def _fix_still_fails(fix: dict) -> bool:
        key = (fix.get("resource") or "", fix.get("rule_id") or "")
        if key in remaining:
            return True
        # FixEntry without resource: fall back to rule_id only.
        if not fix.get("resource") and fix.get("rule_id") in remaining_rules:
            return True
        return False

    effective: list[dict] = []
    # Per-file: if any claimed fix on a file still fails, exclude the whole
    # file so we never PR a mixed effective/ineffective rewrite.
    by_file: dict[str, list[dict]] = {}
    for fix in all_fixes:
        by_file.setdefault(fix["file_path"], []).append(fix)

    for file_path, fixes_in_file in by_file.items():
        still_failing = [f for f in fixes_in_file if _fix_still_fails(f)]
        if still_failing:
            print(
                f"  ⚠ [trivy-only] {len(still_failing)}/{len(fixes_in_file)} "
                f"fix(es) in {os.path.basename(file_path)} still fail Trivy "
                f"— routing to manual review (will not open a fix PR)"
            )
            for f in fixes_in_file:
                needs_review.append({
                    "rule_id": f["rule_id"],
                    "resource": f.get("resource"),
                    "resolution": f.get("description") or "",
                    "reason": "automated fix did not clear Trivy finding",
                })
            continue
        effective.extend(fixes_in_file)

    if all_fixes and not effective:
        print("  [trivy-only] no LLM fix cleared its Trivy finding")
    elif len(effective) < len(all_fixes):
        print(
            f"  [trivy-only] {len(effective)}/{len(all_fixes)} fix(es) "
            f"verified clear by re-scan"
        )
    return effective, needs_review


def _create_manual_review_prs(needs_review: list[dict], account_label: str,
                              run_id: str | None) -> list[dict]:
    """Turn unfixable trivy findings into review-only PRs so they appear
    in the dashboard Approvals queue instead of being silently dropped.

    Each PR carries a ``drift-reports/…/*.md`` commit (GitHub requires at
    least one commit between head and base) — not a Terraform change.  The
    body explains why the automated fix was rejected.

    Reuses the security_only path end to end: merge auto-adds exceptions
    for the (resource, rule_id) pairs via auto_add_exceptions_on_merge;
    reject closes the PR and the finding resurfaces on the next scan."""
    import agent as _ag
    if not needs_review:
        return []
    from drift_reconciler.pending_applies import create_pending_apply, set_security_fixes

    _ag.report_stage(run_id, "trivy_only_review")

    by_resource: dict[str, list[dict]] = {}
    for item in needs_review:
        by_resource.setdefault(item.get("resource") or "unknown", []).append(item)

    pr_urls: list[dict] = []
    for resource_addr, items in by_resource.items():
        # Dedup — one open review PR per resource, same guard as fix PRs.
        existing = _ag.drift_history.get_open_event(resource_addr, account_label, "security_only")
        if existing:
            print(f"  ⏭  {resource_addr}: open review PR #{existing['pr_number']} "
                  f"already exists — skipping")
            continue

        findings = "\n".join(
            f"- **`{i['rule_id']}`** — {i.get('reason', 'needs review')}"
            for i in items
        )
        reasons = "\n".join(
            f"{i['rule_id']} ({i.get('resource')}): {i.get('resolution', '')[:200]}"
            for i in items
        )
        count = len(items)
        # Markdown report so create_pull has a real commit.  Empty
        # review_only branches used to 422 ("No commits between…") and
        # mark the whole trivy_only scan failed at trivy_only_review.
        report_body = (
            f"# Manual review: `{resource_addr}`\n\n"
            f"{findings}\n\n"
            f"### Why no automated fix\n\n```text\n{reasons}\n```\n"
        )
        report_path = _ag.gi.drift_report_repo_path(
            account_label, f"manual-review.{resource_addr}"
        )
        try:
            pr = _ag.gi.create_drift_pr(
                resource_id=resource_addr,
                pr_title=(f"Manual review: {resource_addr} "
                          f"({count} finding{'s' if count != 1 else ''})"),
                drift_summary=findings,
                plan_output=reasons,
                file_path=report_path,
                file_content=report_body,
                risk_level="LOW",
                account_label=account_label,
                security=True,
                review_only=True,
            )
        except Exception as exc:
            # One resource's GitHub failure must not fail the whole scan —
            # remaining resources still get review PRs, and the run stays
            # complete with whatever succeeded.
            print(f"  ⚠ Manual review PR failed for {resource_addr}: {exc}")
            continue
        if pr is None:
            continue
        pr_urls.append({"url": pr.html_url, "type": "manual"})
        create_pending_apply(pr.number, account_label, "security_only", review_only=True)
        # Persist the (resource, rule_id) pairs under review so Except can
        # write security exceptions (Approve/Merge is not offered for these).
        pairs = sorted({(i.get("resource"), i["rule_id"]) for i in items if i.get("resource")})
        if pairs:
            set_security_fixes(
                pr.number, account_label,
                [{"resource_address": r, "rule_id": rid} for r, rid in pairs],
            )
    return pr_urls


def finalize_trivy_only_scan(run_id: str | None, results: dict) -> None:
    import agent as _ag
    """Mark a finished trivy_only run complete — including when the only
    outcome is manual-review PRs after fix rejections.  ``failed`` is
    reserved for unhandled exceptions in the caller."""
    if not run_id:
        return
    from scan_runs import update_scan_run
    from datetime import datetime as dt, timezone

    pr_urls = results.get("pr_urls") or []
    needs_review = results.get("needs_review") or []
    summary = {
        "mode": "trivy_only",
        "security": {
            "found": len(pr_urls) > 0,
            "count": len(pr_urls),
            "pr_links": [r["url"] for r in pr_urls],
            "needs_review": needs_review,
        },
    }
    update_scan_run(
        run_id,
        status="complete",
        completed_at=dt.now(timezone.utc).isoformat(),
        result_summary=summary,
        pr_links=[r["url"] for r in pr_urls] if pr_urls else None,
    )


def run_trivy_only_scan(tf_dir: str, account_label: str, scope: str, run_id: str | None = None) -> dict:
    import agent as _ag
    """Standalone Trivy security scan — no drift detection, no reconcile agent.

    Copies ``.tf`` files to a temp directory, scans for misconfigurations,
    filters out suppressed issues via the exception registry, attempts
    automatic fixes, **re-scans to verify** each fix cleared Trivy, then
    opens **one** ``security_only`` PR for all verified file patches — plus
    one review-only PR per resource whose findings could not be auto-fixed.

    Returns ``{"pr_urls": [...], "needs_review": [...]}`` — ``pr_urls`` is
    a list of ``{"url": str, "type": "security_only"|"manual"}`` dicts, and
    ``needs_review`` lists the findings that could not be auto-fixed (now
    mirrored by the review-only PRs).
    """
    from drift_reconciler.formatting_drift_json import check_security_suppression
    from drift_reconciler.pending_applies import create_pending_apply, set_security_fixes

    _ag.report_stage(run_id, "trivy_only_scan")

    tmpdir = tempfile.mkdtemp(prefix="trivy_only_")

    try:
        # ── Copy .tf tree into the temp workspace (same scope as in-place scan) ─
        copy_tf_tree(tf_dir, tmpdir)

        # ── Scan ──────────────────────────────────────────────────────
        raw = _ag._run_trivy(tmpdir)
        if "error" in raw:
            print(f"  [trivy-only] Scan error: {raw['error']}")
            return {"pr_urls": [], "needs_review": []}

        raw_issues = _extract_issues(raw, tmpdir)
        if not raw_issues:
            print("  [trivy-only] No issues found.")
            return {"pr_urls": [], "needs_review": []}

        # ── Filter suppressed (before the summary log) ────────────────
        # Trivy always reports live misconfigs; exceptions only mean we
        # already accepted them — do not log those as actionable issues.
        suppressed: list[tuple[str, dict]] = []
        kept: list[dict] = []
        for i in raw_issues:
            exc_row = None
            if i.get("resource") and i.get("rule_id"):
                exc_row = check_security_suppression(
                    i["resource"], i["rule_id"], scope
                )
            if exc_row is not None:
                suppressed.append(
                    (f"{i['resource']} ({i['rule_id']})", exc_row)
                )
            else:
                kept.append(i)
        issues = kept
        total = len(raw_issues)

        if suppressed and not issues:
            print(
                f"  [trivy-only] Trivy reported {total} finding(s); "
                f"all already excepted for {scope} — nothing to do."
            )
            for label, exc_row in suppressed:
                unmanaged_scanner.print_exception_skip(label, exc_row)
            return {"pr_urls": [], "needs_review": []}

        if suppressed:
            labels = [label for label, _ in suppressed]
            print(
                f"  [trivy-only] Trivy reported {total} finding(s): "
                f"{len(suppressed)} already excepted, "
                f"{len(issues)} to evaluate"
            )
            print(
                f"  {len(suppressed)} security finding(s) excepted for "
                f"{scope} — these will be skipped: {', '.join(labels)}"
            )
            for label, exc_row in suppressed:
                unmanaged_scanner.print_exception_skip(label, exc_row)
        else:
            print(f"  [trivy-only] {len(issues)} issue(s) found")
            print(
                f"  No security findings are currently excepted for {scope} — "
                f"all {len(issues)} finding(s) will be evaluated normally."
            )

        # ── Apply fixes, then verify with a re-scan ───────────────────
        fix_state: TrivyState = {
            "tf_dir": tmpdir,
            "scan_results": [],
            "issues": issues,
            "fixes_applied": [],
            "needs_review": [],
            "iteration": 0,
            "max_iterations": 3,
            "passed": False,
            "trivy_error": False,
            "messages": [],
            "baseline_issues": [],
            "baseline_captured": False,
        }
        result = _ag.fix_issues(fix_state)
        all_fixes = result.get("fixes_applied", [])
        needs_review = result.get("needs_review", [])

        if all_fixes:
            all_fixes, needs_review = _verify_fixes_cleared(
                tmpdir, all_fixes, needs_review,
            )

        review_prs = _create_manual_review_prs(needs_review, account_label, run_id)
        if not all_fixes:
            print("  [trivy-only] No verified fixes to open a PR for.")
            return {"pr_urls": review_prs, "needs_review": needs_review}

        files_touched = len({f["file_path"] for f in all_fixes})
        print(f"  [trivy-only] {len(all_fixes)} verified fix(es) across "
              f"{files_touched} file(s)")

        # ── One security PR per scope (all files) ─────────────────────
        _sev_to_risk = {"CRITICAL": "HIGH", "HIGH": "HIGH", "MEDIUM": "MEDIUM"}
        _SEVERITY_RANK_LOOKUP = {
            "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4,
        }
        derived_resource_id = "trivy-security"

        _ag.report_stage(run_id, "trivy_only_pr")

        existing = _ag.drift_history.get_open_event(
            derived_resource_id, account_label, "security_only",
        )
        if not existing:
            # Honor legacy per-file dedup ids while older PRs are still open.
            seen_legacy: set[str] = set()
            for fix in all_fixes:
                legacy_id = (
                    "trivy-security-"
                    + os.path.basename(fix["file_path"]).replace(".tf", "")
                )
                if legacy_id in seen_legacy:
                    continue
                seen_legacy.add(legacy_id)
                existing = _ag.drift_history.get_open_event(
                    legacy_id, account_label, "security_only",
                )
                if existing:
                    break
        if existing:
            print(f"  Skipping {derived_resource_id}: open security PR "
                  f"#{existing['pr_number']} already exists")
            return {"pr_urls": review_prs, "needs_review": needs_review}

        by_file: dict[str, list[dict]] = {}
        for fix in all_fixes:
            by_file.setdefault(fix["file_path"], []).append(fix)

        file_payloads: list[tuple[str, str, str]] = []
        for tmp_file_path, _fixes_in_file in by_file.items():
            try:
                rel = os.path.relpath(tmp_file_path, tmpdir)
                if rel.startswith(".."):
                    rel = os.path.basename(tmp_file_path)
            except ValueError:
                rel = os.path.basename(tmp_file_path)
            original_path = os.path.join(tf_dir, rel)
            with open(tmp_file_path, encoding="utf-8") as f:
                patched_content = f.read()
            rel_posix = rel.replace("\\", "/")
            original_content = ""
            if os.path.isfile(original_path):
                with open(original_path, encoding="utf-8") as f:
                    original_content = f.read()
                if original_content == patched_content:
                    print(f"  ⏭  {rel_posix}: local already matches patched "
                          f"content — omitting from PR")
                    continue
                diff_lines = list(difflib.unified_diff(
                    original_content.splitlines(keepends=True),
                    patched_content.splitlines(keepends=True),
                    fromfile=f"a/{rel_posix}",
                    tofile=f"b/{rel_posix}",
                ))
                plan_chunk = "".join(diff_lines)
                repo_path = _ag.gi.to_repo_relative_path(original_path)
            else:
                plan_chunk = f"(original {rel_posix} not found — full patched content)"
                repo_path = (
                    f"drift-reports/{_ag.gi._safe_label(account_label)}/"
                    f"security-{os.path.basename(rel)}"
                )
            # (repo_path, patched, diff, original) — original feeds rollback baseline
            file_payloads.append(
                (repo_path, patched_content, plan_chunk, original_content)
            )

        if not file_payloads:
            print("  [trivy-only] Verified fixes already present locally — "
                  "no PR needed.")
            return {"pr_urls": review_prs, "needs_review": needs_review}

        rule_severity: dict[str, str] = {}
        for iss in issues:
            rid = iss.get("rule_id", "")
            if not rid:
                continue
            sev = (iss.get("severity") or "UNKNOWN").upper()
            cur = rule_severity.get(rid)
            if cur is None or _SEVERITY_RANK_LOOKUP.get(sev, 4) < _SEVERITY_RANK_LOOKUP.get(cur, 4):
                rule_severity[rid] = sev
        highest_sev = "LOW"
        for fix in all_fixes:
            sev = rule_severity.get(fix["rule_id"], "UNKNOWN")
            if _SEVERITY_RANK_LOOKUP.get(sev, 4) <= _SEVERITY_RANK_LOOKUP.get(highest_sev, 4):
                highest_sev = sev
        risk_level = _sev_to_risk.get(highest_sev, "LOW")

        count = len(all_fixes)
        drift_summary = "\n".join(
            f"- **`{f['rule_id']}`** ({f.get('resource') or '?'}): {f['description']}"
            for f in all_fixes
        )
        plan_output = "\n".join(chunk for _p, _c, chunk, _o in file_payloads)
        primary_path, primary_content, _, primary_original = file_payloads[0]
        additional = [(p, c) for p, c, _d, _o in file_payloads[1:]]
        files_label = ", ".join(os.path.basename(p) for p, _c, _d, _o in file_payloads)
        pr_title = (
            f"Security fix: {count} issue{'s' if count != 1 else ''} "
            f"({files_label})"
        )

        from drift_reconciler.drift_baseline import tf_file_baseline
        import drift_reconciler.drift_history as drift_history

        pr_urls: list[dict] = []
        # History rows are written per file below (whole-file rollback
        # baselines) — skip the default single append_entry.
        pr = _ag.gi.create_drift_pr(
            resource_id=derived_resource_id,
            pr_title=pr_title,
            drift_summary=drift_summary,
            plan_output=plan_output,
            file_path=primary_path,
            file_content=primary_content,
            risk_level=risk_level,
            account_label=account_label,
            security=True,
            additional_files=additional or None,
            changes=tf_file_baseline(primary_original, primary_content)
            if primary_original else None,
            append_history=False,
        )
        if pr is not None:
            pr_urls.append({"url": pr.html_url, "type": "security_only"})
            create_pending_apply(pr.number, account_label, "security_only")
            region = drift_history.resolve_region(account_label)
            baselines_stored = 0
            for idx, (repo_path, patched, _chunk, original) in enumerate(file_payloads):
                if not original:
                    print(f"  ⚠ {repo_path}: no original content — "
                          f"rollback baseline not stored")
                    continue
                # First file keeps resource_id=trivy-security so open-PR
                # dedup (get_open_event) still finds this scan's row.
                rid = (
                    derived_resource_id if idx == 0
                    else f"trivy-security:{os.path.basename(repo_path)}"
                )
                drift_history.append_entry(
                    resource_id=rid,
                    account_label=account_label,
                    region=region,
                    pr_number=pr.number,
                    pr_type="security_only",
                    severity=risk_level,
                    fields_changed=["__tf_file__"],
                    drift_summary=drift_summary,
                    changes_jsonb=tf_file_baseline(original, patched),
                    file_path=repo_path,
                )
                baselines_stored += 1
            print(f"  [trivy-only] stored {baselines_stored} file baseline(s) "
                  f"for rollback on PR #{pr.number}")
            pairs = sorted({
                (fix.get("resource") or "", fix["rule_id"])
                for fix in all_fixes
                if fix.get("rule_id")
            })
            if not any(r for r, _ in pairs):
                pairs = sorted({
                    (i.get("resource") or "", fix["rule_id"])
                    for fix in all_fixes
                    for i in issues
                    if i.get("rule_id") == fix.get("rule_id") and i.get("resource")
                })
            pairs = [(r, rid) for r, rid in pairs if r and rid]
            if pairs:
                ok = set_security_fixes(
                    pr.number, account_label,
                    [{"resource_address": r, "rule_id": rid} for r, rid in pairs],
                )
                print(f"  [trivy-only] recorded {len(pairs)} fix pair(s) on "
                      f"pending_applies for PR #{pr.number} "
                      f"({'ok' if ok else 'FAILED'})")
            else:
                print(f"  ⚠ security PR #{pr.number}: no (resource, rule_id) "
                      f"pairs recorded — Except cannot auto-add exceptions")

        return {"pr_urls": pr_urls + review_prs, "needs_review": needs_review}

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
