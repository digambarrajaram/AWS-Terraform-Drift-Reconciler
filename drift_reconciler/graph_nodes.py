"""LangGraph pipeline nodes (must not import graph_wiring)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pagerduty_alert as pga
import slack_notify as slack
import github_integration as gi
import drift_reconciler.drift_history as drift_history
import unmanaged_scanner
from drift_reconciler.environment_credentials import (
    get_aws_session,
    _resolve_env_credentials,
)
from trivy_agent import graph as trivy_graph, State as TrivyState
from trivy_agent import _run_trivy, _extract_issues, copy_tf_tree
from scan_runs import report_stage
from drift_findings import State
from terraform_ops import _ensure_terraform_init

def trivy_gate(state: State):
    import agent as _ag
    _ag.report_stage(state.get("run_id"), "trivy_gate")
    """Run the Trivy security scan→fix→scan loop against the proposed
    drift-reconciliation HCL before alerting or creating a PR."""
    if not state.get("drift_detected") or not state.get("drift_findings"):
        return {"trivy_scanned": False}

    # Only scan findings that have a file_path and actual changes.
    findings = state["drift_findings"]
    actionable = [f for f in findings
                   if f.get("file_path") and f.get("changes")
                   and f.get("status") != "externally_managed"]
    if not actionable:
        return {"trivy_scanned": False}

    tmpdir = tempfile.mkdtemp(prefix="trivy_gate_")

    # Take a baseline scan of the ORIGINAL code before applying any
    # drift fixes so the Trivy loop can distinguish pre-existing
    # issues from regressions introduced by the LLM's patch.
    src_dir = os.path.dirname(os.path.abspath(actionable[0]["file_path"]))
    scan_root = os.path.abspath(_ag._tf_dir) if _ag._tf_dir else src_dir
    baseline_raw = _run_trivy(scan_root)
    baseline_issues: list[dict] = []
    if "error" not in baseline_raw:
        baseline_issues = _extract_issues(baseline_raw, scan_root)
    print(f"  [trivy-gate] Baseline scan: {len(baseline_issues)} pre-existing issue(s)")

    print(f"  [trivy-gate] Running security scan on proposed drift fixes …")

    try:
        # Copy the full terraform tree so Trivy sees the same scope as the
        # baseline scan (nested modules, not only top-level .tf files).
        copy_tf_tree(scan_root, tmpdir)

        # Apply code_to_reality patches (HCL → drift after / live values).
        for f in actionable:
            try:
                rel = os.path.relpath(os.path.abspath(f["file_path"]), scan_root)
            except ValueError:
                rel = os.path.basename(f["file_path"])
            tf_file = os.path.join(tmpdir, rel)
            if not os.path.isfile(tf_file):
                tf_file = os.path.join(tmpdir, os.path.basename(f["file_path"]))
            if os.path.isfile(tf_file):
                patch_changes = _ag.gi.filter_patchable_changes(f.get("changes") or {})
                if not patch_changes:
                    continue
                patched = _ag.gi.apply_changes_to_file(
                    tf_file, f["resource_id"], patch_changes,
                    value_key="after",
                )
                with open(tf_file, "w", encoding="utf-8") as fh:
                    fh.write(patched)

        # Invoke the self-contained trivy scan→fix→scan loop.
        trivy_initial: TrivyState = {
            "tf_dir": tmpdir,
            "scan_results": [],
            "issues": [],
            "fixes_applied": [],
            "needs_review": [],
            "iteration": 0,
            "max_iterations": 3,
            "passed": False,
            "trivy_error": False,
            "messages": [],
            "baseline_issues": baseline_issues,
            "baseline_captured": True,
        }
        trivy_result = trivy_graph.invoke(trivy_initial)

        # Enrich each finding with trivy scan metadata.
        remaining_issues = trivy_result.get("issues", [])
        pre_existing = [i for i in remaining_issues if i.get("origin") == "pre-existing"]
        newly_introduced = [i for i in remaining_issues if i.get("origin") != "pre-existing"]

        for f in findings:
            f["trivy_passed"] = trivy_result.get("passed", False)
            f["trivy_error"] = trivy_result.get("trivy_error", False)
            f["trivy_pre_existing_count"] = len(pre_existing)
            f["trivy_newly_introduced_count"] = len(newly_introduced)
        if trivy_result.get("fixes_applied"):
            for f in findings:
                f["trivy_security_fixes"] = len(trivy_result["fixes_applied"])

        fixes_count = len(trivy_result.get("fixes_applied", []))
        if fixes_count:
            print(f"  [trivy-gate] Applied {fixes_count} security fix(es) to proposed drift HCL")
        if newly_introduced:
            print(f"  [trivy-gate] {len(newly_introduced)} newly-introduced finding(s) (may need review)")
        if pre_existing:
            print(f"  [trivy-gate] {len(pre_existing)} pre-existing finding(s) (not caused by this fix, not auto-fixed)")
        if not remaining_issues and trivy_result.get("passed") and not trivy_result.get("trivy_error"):
            print(f"  [trivy-gate] ✓ Proposed drift fix passes security scan")
        if trivy_result.get("trivy_error"):
            print(f"  [trivy-gate] ⚠ Trivy scan encountered an error — proceeding without security validation")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {"trivy_scanned": True, "drift_findings": findings}


def _load_routing_rules() -> dict[str, str]:
    """Return ``{severity: channel}`` from Supabase, with scope-specific
    rules overriding global defaults.  Falls back to hardcoded defaults
    if Supabase is unreachable or the table is empty."""
    import os as _os
    import requests as _requests
    import agent as _ag
    try:
        url = _os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError("no Supabase creds")
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        # Fetch all rules at once — ~6 rows max, no pagination needed.
        resp = _requests.get(
            f"{url}/rest/v1/severity_routing_rules?select=severity,channel,scope",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        rows = resp.json() if resp.text else []
        if not rows:
            raise RuntimeError("empty table")

        # Global rules first (scope is null), then scope-specific overrides.
        rules: dict[str, str] = {}
        for r in rows:
            if r.get("scope") is None:
                rules[r["severity"]] = r["channel"]
        for r in rows:
            if r.get("scope") == _ag._account_label:
                rules[r["severity"]] = r["channel"]
        return rules
    except Exception:
        # Hardcoded fallback — never silently drop alerts.
        return {"HIGH": "pagerduty", "MEDIUM": "slack", "LOW": "slack"}


def _finding_channel(rules: dict[str, str], finding: dict) -> str:
    """Resolve routing channel for a finding (case-insensitive severity)."""
    raw = finding.get("risk_level") or "LOW"
    sev = str(raw).strip().upper()
    return rules.get(sev) or rules.get(raw) or "none"


def _pd_dedup_key(finding: dict, run_id: str | None = None) -> str:
    """Build a dedup key that still allows phone pages on new scans.

    Dashboard Send Test omits dedup_key → always pages.
    A stable ``drift-<resource>`` key matches an open incident and PagerDuty
    accepts the event (202) **without** re-calling. Fingerprint the drift
    payload and include UTC day (+ run_id when present) so a scan pages at
    least once per day even if the prior incident is still open.
    """
    import hashlib
    from datetime import datetime, timezone

    rid = finding.get("resource_id") or "unknown"
    fingerprint = (
        finding.get("drift_summary")
        or finding.get("plan_output")
        or json.dumps(finding.get("changes") or {}, sort_keys=True, default=str)
    )
    digest = hashlib.sha256(str(fingerprint).encode("utf-8")).hexdigest()[:10]
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    # Include a short run suffix so two scans the same day still page when
    # the operator is actively re-testing; keep it short for the 255 limit.
    run_suffix = ""
    if run_id:
        run_suffix = "-" + str(run_id).replace("-", "")[-8:]
    return f"drift-{rid}-{digest}-{day}{run_suffix}"


def drift_alert(state: State):
    import agent as _ag
    _ag.report_stage(state.get("run_id"), "alert_agent")
    """Route findings by severity using Supabase routing rules, falling
    back to hardcoded HIGH→PagerDuty / else→Slack if unreachable.

    Channel semantics:
      - pagerduty → page PagerDuty *and* post Slack (if webhook configured)
      - slack     → Slack only
      - none      → silence
    """
    if not state.get("drift_detected"):
        return {"messages": [], "alerts_sent": {"pagerduty": 0, "slack": 0}}

    active = [f for f in state["drift_findings"]
              if f.get("status") != "externally_managed"]
    if not active:
        return {"messages": [], "alerts_sent": {"pagerduty": 0, "slack": 0}}

    rules = _load_routing_rules()
    # Normalize keys so DB quirks (mixed case) never silently drop alerts.
    rules = {str(k).strip().upper(): v for k, v in rules.items()}
    print(f"  [alert_agent] routing rules for {_ag._account_label}: {rules}")

    pd_findings = [f for f in active if _finding_channel(rules, f) == "pagerduty"]
    # Slack gets explicit slack-routed findings PLUS pagerduty-routed ones
    # (so "PagerDuty for any severity" still produces Slack messages).
    slack_findings = [
        f for f in active if _finding_channel(rules, f) in ("slack", "pagerduty")
    ]
    silenced = sum(1 for f in active if _finding_channel(rules, f) == "none")
    print(
        f"  [alert_agent] {len(active)} finding(s): "
        f"{len(pd_findings)} → pagerduty, {len(slack_findings)} → slack "
        f"(incl. PD fan-out), {silenced} silenced (channel=none)"
    )
    for f in active:
        print(
            f"  [alert_agent]   {f.get('resource_id')} "
            f"risk={f.get('risk_level')!r} → {_finding_channel(rules, f)}"
        )

    alert_errors: list[str] = []
    if active and not pd_findings:
        alert_errors.append(
            "No findings routed to PagerDuty (Slack-only or silenced). "
            "Send Test always pages and bypasses severity rules — set HIGH/MEDIUM/LOW "
            "→ PagerDuty on Alerts and Save each row."
        )

    # PagerDuty → one page per finding.
    # Use severity=critical so PD high-urgency phone/SMS rules fire.
    # Dedup includes drift fingerprint + day + run so we don't silently no-op
    # against an already-open incident the way a stable drift-<resource> key did
    # (Send Test has no dedup_key → always called; drift alerts did not).
    pd_sent = 0
    run_id = state.get("run_id")
    for finding in pd_findings:
        if finding.get("status") in unmanaged_scanner.UNMANAGED_STATUSES:
            event_type = "Unmanaged resource"
        else:
            event_type = "Drift detected"
        risk = finding.get("risk_level") or "LOW"
        summary = f"{event_type}: {finding['resource_id']} [{risk}]"
        cost = finding.get("cost_impact")
        if cost:
            summary += f" (${cost['monthly_estimate_usd']:.2f}/mo)"
        dedup = _pd_dedup_key(finding, run_id)
        result = pga.trigger_pagerduty_alert(
            summary=summary,
            severity="critical",
            source="terraform-drift-engine",
            dedup_key=dedup,
            account_label=_ag._account_label,
            error_detail=alert_errors,
            custom_details={
                "resource_id": finding.get("resource_id"),
                "risk_level": risk,
                "account": _ag._account_label,
                "run_id": run_id,
            },
        )
        if result:  # PagerDuty returns {} on failure, non-empty dict on dispatch
            pd_sent += 1
            print(
                f"  [alert_agent] PagerDuty accepted: {finding['resource_id']} "
                f"dedup={dedup!r}"
            )
        else:
            print(f"  [alert_agent] PagerDuty FAILED for {finding['resource_id']}")

    # Slack → batched (includes PD-routed findings for visibility).
    slack_sent = 0
    if slack_findings:
        try:
            slack_sent = slack.notify_all(slack_findings, _ag._account_label)
            if slack_sent == 0:
                alert_errors.append(
                    "Slack webhook not configured or message send failed — "
                    "check Alerts → Slack Webhook URL / Send Test"
                )
        except Exception as exc:
            alert_errors.append(f"Slack send failed: {exc}")
            print(f"  [alert_agent] Slack FAILED: {exc}")

    alerts_payload: dict = {"pagerduty": pd_sent, "slack": slack_sent}
    if alert_errors:
        seen: set[str] = set()
        unique_errs = []
        for e in alert_errors:
            if e not in seen:
                seen.add(e)
                unique_errs.append(e)
        alerts_payload["errors"] = unique_errs
        print(f"  [alert_agent] alert errors: {unique_errs}")

    return {"messages": [], "alerts_sent": alerts_payload}
def drift_pr_from_finding(state: State):
    import agent as _ag
    _ag.report_stage(state.get("run_id"), "drift_pr")
    if not state.get("drift_detected"):
        return {"pr_urls": []}

    # Group findings by file_path so changes to the same .tf file
    # ship in one PR instead of N independent PRs.
    by_file: dict[str, list[dict]] = {}
    report_only: list[dict] = []
    for finding in state["drift_findings"]:
        if finding.get("status") == "externally_managed":
            continue
        fp = finding.get("file_path")
        if fp:
            by_file.setdefault(fp, []).append(finding)
        else:
            report_only.append(finding)

    def _already_open(finding: dict, pr_type: str = "fix") -> dict | None:
        """Return the open drift_events row if one exists for this finding, else None."""
        return _ag.drift_history.get_open_event(finding["resource_id"], _ag._account_label, pr_type)

    def _blocked_by_current_open_pr(finding: dict, pr_type: str) -> bool:
        """True when an open PR already covers *this* drift payload.

        If an open PR exists but its stored changes_jsonb is stale (AWS moved
        again — the classic n-1 lag), supersede it so a fresh PR is created.
        """
        existing = _already_open(finding, pr_type)
        if not existing:
            return False
        if _ag.drift_history.open_event_matches_finding(existing, finding):
            print(
                f"  ⏭  {finding['resource_id']}: open PR #{existing['pr_number']} "
                f"already matches current drift — skipping"
            )
            return True
        print(
            f"  ↻  {finding['resource_id']}: open PR #{existing['pr_number']} is stale "
            f"(stored drift ≠ live plan) — superseding and recreating"
        )
        _ag.drift_history.supersede_open_event(
            existing,
            resolution=(
                f"Superseded by newer scan — drift changed since PR "
                f"#{existing.get('pr_number')}"
            ),
        )
        try:
            from drift_reconciler.pending_applies import mark_superseded
            if existing.get("pr_number"):
                mark_superseded(existing["pr_number"], _ag._account_label)
        except Exception as exc:
            print(f"  ⚠ pending_applies supersede failed: {exc}")
        return False

    pr_urls = []
    for file_path, group in by_file.items():
        if len(group) == 1:
            if _blocked_by_current_open_pr(group[0], "fix"):
                continue
            pr = _ag.gi.create_drift_pr_for_mode(group[0], "code_to_reality", account_label=_ag._account_label)
        else:
            # Filter findings that already have a *current* open PR before batching.
            actionable = [f for f in group if not _blocked_by_current_open_pr(f, "batch")]
            if not actionable:
                continue
            pr = _ag.gi.create_drift_pr_for_file(actionable, "code_to_reality", account_label=_ag._account_label)
        if pr is not None:
            # Findings with file_path are always drift fixes (unmanaged
            # findings have file_path=None and land in report_only).
            pr_urls.append({"url": pr.html_url, "type": "drift"})
            # Primary Approve/Reject trigger: every created PR lands in the
            # Approvals page immediately (not waiting for a merge webhook).
            from drift_reconciler.pending_applies import create_pending_apply
            create_pending_apply(pr.number, _ag._account_label,
                                 "fix" if len(group) == 1 else "batch")

    # Active exceptions for this scope, loaded once before any unmanaged
    # PR is created.  Rows are written by auto_add_exceptions_on_merge on
    # merge (resource_type + resource_id_pattern, split from the
    # resource_id) or by hand in the dashboard; the match below mirrors
    # the scanner's suppression semantics (pattern is a substring of the
    # resource name part).
    except_rows = _ag.unmanaged_scanner._load_exceptions(_ag._account_label)

    def _matching_exception(finding: dict) -> dict | None:
        rid = finding.get("resource_id") or ""
        if "." not in rid:
            return None
        rtype, rname = rid.split(".", 1)
        for exc in except_rows:
            if exc.get("resource_type") != rtype:
                continue
            pattern = exc.get("resource_id_pattern") or ""
            if pattern and pattern in rname:
                return exc
        return None

    for finding in report_only:
        # Second exception guard at PR time: exceptions may have been
        # added since the diff ran (the merge handler auto-adds one for
        # every merged unmanaged PR) — an excepted resource must never
        # generate a new PR.
        matched_exc = _matching_exception(finding)
        if matched_exc is not None:
            _ag.unmanaged_scanner.print_exception_skip(
                finding["resource_id"], matched_exc
            )
            continue
        print(f"  {finding['resource_id']}: no exception on file — creating PR")
        is_unmanaged = finding.get("status") in _ag.unmanaged_scanner.UNMANAGED_STATUSES
        dedup_type = "unmanaged" if is_unmanaged else "fix"
        if _blocked_by_current_open_pr(finding, dedup_type):
            continue
        pr = _ag.gi.create_drift_pr_for_mode(finding, "code_to_reality", account_label=_ag._account_label)
        if pr is not None:
            pr_urls.append({"url": pr.html_url, "type": "unmanaged" if is_unmanaged else "drift"})
            from drift_reconciler.pending_applies import create_pending_apply
            create_pending_apply(pr.number, _ag._account_label,
                                 "unmanaged" if is_unmanaged else "fix")

    return {"pr_urls": pr_urls}


def unmanaged_scan_node(state: State):
    import agent as _ag
    _ag.report_stage(state.get("run_id"), "unmanaged_scan")
    """Enumerate live AWS resources, subtract what Terraform manages.

    Runs before the reconcile agent when --scan-unmanaged is set.
    Findings are appended to drift_findings so the existing alert/PR
    nodes pick them up without changes."""
    if _ag._tf_dir is None:
        return {"messages": []}

    print("\n--- Unmanaged resource scan ---")
    try:
        # Resolve environment row and build AWS session.
        from drift_reconciler.scope_resolution import (
            fetch_environment_row,
            user_id_for_scope_context,
        )

        run_id = state.get("run_id")
        uid = user_id_for_scope_context(_ag._account_label, run_id)
        env_dict = fetch_environment_row(_ag._account_label, user_id=uid)
        session = _ag.get_aws_session(env_dict, use_scan_role=True)
        live = _ag.unmanaged_scanner.scan_unmanaged_resources(session, _ag._region)
    except Exception as e:
        raise

    if not live:
        print("  (no live resources found)")
        return {"messages": []}

    # Subtract tracked resources against an actually-initialized backend —
    # same live-check as the drift path (_ensure_terraform_init keys off
    # .terraform/terraform.tfstate and forces -reconfigure when the cached
    # bucket no longer matches the environment row).  Without it, a fresh
    # or stale clone makes `terraform show` fail with "Backend
    # initialization required" and load_managed_resources fail-softs to
    # [] — every live resource flagged unmanaged.  On init failure we
    # report nothing rather than invent findings.
    sub_env = _resolve_env_credentials(env_dict, tf_dir=_ag._tf_dir)
    backend_config = {}
    if env_dict.get("tf_state_bucket"):
        backend_config["bucket"] = env_dict["tf_state_bucket"]
    if env_dict.get("tf_lock_table"):
        backend_config["dynamodb_table"] = env_dict["tf_lock_table"]
    if env_dict.get("region"):
        backend_config["region"] = env_dict["region"]
    init_error = _ag._ensure_terraform_init(_ag._tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        # Abort the run — returning empty findings would look identical to
        # "scan ran fine, nothing unmanaged," which hides a real failure
        # (provider timeout, backend unreachable, etc.).  Raising lets the
        # outer scan_runs finalizer mark status=failed with the init error.
        print(f"  ⚠ terraform init failed for {_ag._account_label}: {init_error[:400]} — "
              f"aborting unmanaged scan (diff skipped)")
        raise RuntimeError(
            f"terraform init failed for {_ag._account_label} — unmanaged scan "
            f"aborted (could not load state to subtract managed resources):\n"
            f"{init_error[:800]}"
        )
    managed = _ag.unmanaged_scanner.load_managed_resources(_ag._tf_dir, env=sub_env)
    findings = _ag.unmanaged_scanner.diff_unmanaged(live, managed, region=_ag._region, tf_dir=_ag._tf_dir, scope=_ag._account_label)

    if not findings:
        print("  (every live resource is tracked in state)")
        return {"messages": []}

    print(f"  {len(findings)} unmanaged resource(s) found:")
    for f in findings:
        cost = f.get("cost_impact")
        cost_line = ""
        if cost:
            cost_line = f"  — ${cost['monthly_estimate_usd']:.2f}/mo"
        print(f"    [{f['risk_level']}] {f['resource_id']}{cost_line}")

    # Merge into drift_findings — downstream alert/PR nodes iterate
    # this list and will surface unmanaged entries alongside drift.
    existing = state.get("drift_findings") or []
    return {"drift_findings": existing + findings, "drift_detected": True}


