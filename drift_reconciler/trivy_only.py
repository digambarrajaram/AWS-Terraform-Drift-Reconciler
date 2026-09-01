"""Standalone --trivy-only scan path."""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import github_integration as gi
import drift_reconciler.drift_history as drift_history
import unmanaged_scanner
from trivy_agent import _run_trivy, _extract_issues, fix_issues, State as TrivyState
from scan_runs import report_stage

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
    automatic fixes, and creates one PR per modified file with
    ``pr_type="security_only"`` — plus one review-only PR (no file diff)
    per resource whose findings could not be auto-fixed, so they surface
    in the approval queue instead of being silently dropped.

    Returns ``{"pr_urls": [...], "needs_review": [...]}`` — ``pr_urls`` is
    a list of ``{"url": str, "type": "security_only"|"manual"}`` dicts, and
    ``needs_review`` lists the findings that could not be auto-fixed (now
    mirrored by the review-only PRs).
    """
    from formatting_drift_json import check_security_suppression

    _ag.report_stage(run_id, "trivy_only_scan")

    tmpdir = tempfile.mkdtemp(prefix="trivy_only_")

    try:
        # ── Copy .tf files into the temp workspace ────────────────────
        for item in os.listdir(tf_dir):
            s = os.path.join(tf_dir, item)
            d = os.path.join(tmpdir, item)
            if os.path.isfile(s) and item.endswith(".tf"):
                shutil.copy2(s, d)

        # ── Scan ──────────────────────────────────────────────────────
        raw = _ag._run_trivy(tmpdir)
        if "error" in raw:
            print(f"  [trivy-only] Scan error: {raw['error']}")
            return {"pr_urls": [], "needs_review": []}

        issues = _extract_issues(raw, tmpdir)
        if not issues:
            print("  [trivy-only] No issues found.")
            return {"pr_urls": [], "needs_review": []}

        print(f"  [trivy-only] {len(issues)} issue(s) found")

        # ── Filter suppressed ─────────────────────────────────────────
        suppressed: list[tuple[str, dict]] = []
        kept: list[dict] = []
        for i in issues:
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
        if suppressed:
            labels = [label for label, _ in suppressed]
            print(
                f"  {len(suppressed)} security finding(s) excepted for "
                f"{scope} — these will be skipped: {', '.join(labels)}"
            )
            for label, exc_row in suppressed:
                unmanaged_scanner.print_exception_skip(label, exc_row)
            print(
                f"  {len(issues)} security finding(s) have no exception on "
                f"file — continuing evaluation"
            )
        else:
            print(
                f"  No security findings are currently excepted for {scope} — "
                f"all {len(issues)} finding(s) will be evaluated normally."
            )

        # ── Apply fixes (single pass — no re-scan loop) ──────────────
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
        review_prs = _create_manual_review_prs(needs_review, account_label, run_id)
        if not all_fixes:
            print("  [trivy-only] No fixes applied.")
            return {"pr_urls": review_prs, "needs_review": needs_review}

        files_touched = len({f["file_path"] for f in all_fixes})
        print(f"  [trivy-only] {len(all_fixes)} fix(es) applied across {files_touched} file(s)")

        # ── Map Trivy severity → drift risk_level vocabulary ──────────
        _sev_to_risk = {"CRITICAL": "HIGH", "HIGH": "HIGH", "MEDIUM": "MEDIUM"}
        # LOW and UNKNOWN default to "LOW"

        # ── Group fixes by file → one PR per file ─────────────────────
        import difflib

        by_file: dict[str, list[dict]] = {}
        for fix in all_fixes:
            by_file.setdefault(fix["file_path"], []).append(fix)

        _ag.report_stage(run_id, "trivy_only_pr")

        pr_urls: list[dict] = []
        for tmp_file_path, fixes_in_file in by_file.items():
            basename = os.path.basename(tmp_file_path)
            derived_resource_id = f"trivy-security-{basename.replace('.tf', '')}"

            # Dedup — don't create a second security PR for the same file
            # while an earlier one is still open.
            existing = _ag.drift_history.get_open_event(derived_resource_id, account_label, "security_only")
            if existing:
                print(f"  Skipping {derived_resource_id}: open security PR "
                      f"#{existing['pr_number']} already exists")
                continue

            # ── Patched content (from tmpdir) ─────────────────────────
            with open(tmp_file_path, encoding="utf-8") as f:
                patched_content = f.read()

            # ── Unified diff against the original file ────────────────
            original_path = os.path.join(tf_dir, basename)
            if os.path.isfile(original_path):
                with open(original_path, encoding="utf-8") as f:
                    original_content = f.read()
                diff_lines = list(difflib.unified_diff(
                    original_content.splitlines(keepends=True),
                    patched_content.splitlines(keepends=True),
                    fromfile=f"a/{basename}",
                    tofile=f"b/{basename}",
                ))
                plan_output = "".join(diff_lines)
                repo_path = _ag.gi.to_repo_relative_path(original_path)
            else:
                plan_output = ("(original file not found — "
                               "full patched content below)")
                repo_path = (
                    f"drift-reports/{_ag.gi._safe_label(account_label)}/"
                    f"security-{basename}"
                )

            # ── Determine highest severity among this file's fixes ────
            # fixes_applied entries carry rule_id but not severity —
            # resolve from the original issues list.
            _SEVERITY_RANK_LOOKUP = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
            rule_severity: dict[str, str] = {}
            for iss in issues:
                rid = iss.get("rule_id", "")
                if rid:
                    sev = (iss.get("severity") or "UNKNOWN").upper()
                    cur = rule_severity.get(rid)
                    if cur is None or _SEVERITY_RANK_LOOKUP.get(sev, 4) < _SEVERITY_RANK_LOOKUP.get(cur, 4):
                        rule_severity[rid] = sev
            highest_sev = "LOW"
            for fix in fixes_in_file:
                sev = rule_severity.get(fix["rule_id"], "UNKNOWN")
                rank = _SEVERITY_RANK_LOOKUP.get(sev, 4)
                if rank <= _SEVERITY_RANK_LOOKUP.get(highest_sev, 4):
                    highest_sev = sev
            risk_level = _sev_to_risk.get(highest_sev, "LOW")

            # ── Markdown summary ──────────────────────────────────────
            count = len(fixes_in_file)
            drift_summary = "\n".join(
                f"- **`{f['rule_id']}`**: {f['description']}"
                for f in fixes_in_file
            )
            pr_title = f"Security fix: {basename} ({count} issue{'s' if count != 1 else ''})"

            pr = _ag.gi.create_drift_pr(
                resource_id=derived_resource_id,
                pr_title=pr_title,
                drift_summary=drift_summary,
                plan_output=plan_output,
                file_path=repo_path,
                file_content=patched_content,
                risk_level=risk_level,
                account_label=account_label,
                security=True,
            )
            if pr is not None:
                pr_urls.append({"url": pr.html_url, "type": "security_only"})
                # Security PRs are file-only (no terraform action) but still
                # need the Approve/Reject flow — without this row they'd
                # never appear in the dashboard queue.
                from drift_reconciler.pending_applies import create_pending_apply, set_security_fixes
                create_pending_apply(pr.number, account_label, "security_only")
                # Persist the (resource_address, rule_id) pairs this PR fixes so
                # Except (real-fix) / Approve (review_only) can write security
                # exceptions for exactly those findings.
                pairs = sorted({
                    (fix.get("resource") or "", fix["rule_id"])
                    for fix in fixes_in_file
                    if fix.get("rule_id")
                })
                # Fallback when FixEntry has no resource (older shape).
                if not any(r for r, _ in pairs):
                    pairs = sorted({
                        (i.get("resource") or "", fix["rule_id"])
                        for fix in fixes_in_file
                        for i in issues
                        if i.get("rule_id") == fix.get("rule_id") and i.get("resource")
                        and (
                            not i.get("target")
                            or os.path.basename(i.get("target") or "") == basename
                        )
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


