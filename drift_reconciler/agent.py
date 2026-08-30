import argparse
from datetime import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Windows terminals often default to cp1252 which can't encode emoji /
# Unicode symbols used in progress output.  Reconfigure early so every
# print() in this module survives regardless of terminal code page.
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('cp1252', 'cp1250', 'cp1251', 'cp1253', 'cp1254', 'cp1255', 'cp1256', 'cp1257', 'cp1258', 'cp437', 'cp850', 'cp852', 'cp866'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.getLogger("botocore").setLevel(logging.ERROR)

_DR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DR)
if _DR not in sys.path:
    sys.path.insert(0, _DR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from typing import Annotated  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
import pagerduty_alert as pga  # noqa: E402
import slack_notify as slack  # noqa: E402
from drift_reconciler.environment_credentials import get_aws_session, _resolve_env_credentials  # noqa: E402
import drift_reconciler.drift_history as drift_history  # noqa: E402
import github_integration as gi  # noqa: E402
import json  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from trivy_agent import graph as trivy_graph, State as TrivyState  # noqa: E402
from trivy_agent import _run_trivy, _extract_issues, fix_issues  # noqa: E402
from scan_runs import report_stage  # noqa: E402
import unmanaged_scanner  # noqa: E402

# Resolved at startup from CLI args (or env fallback).
_account_label = "default"
_region = os.environ.get("AWS_REGION", "us-east-1")
_tf_dir: str | None = None
_run_id: str | None = None

_drift_script_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "formatting_drift_json.py"
)

from drift_reconciler.llm_client import _get_llm  # noqa: E402

# --- re-exports (tests patch/call these on the agent module) ---
from terraform_errors import (  # noqa: E402
    _strip_ansi,
    humanize_terraform_error,
    humanize_rollback_error,
)
from terraform_ops import (  # noqa: E402
    _terraform_sub_env_for_scope,
    _ensure_terraform_init,
    get_terraform_drift_data,
)
from hcl_patch import _apply_changes_to_file  # noqa: E402
from drift_findings import (  # noqa: E402
    State,
    map_risk,
    _min_network_risk,
    build_drift_summary,
    build_drift_findings,
    agent_node,
)
from graph_nodes import (  # noqa: E402
    trivy_gate,
    _load_routing_rules,
    drift_alert,
    drift_pr_from_finding,
    unmanaged_scan_node,
)
from trivy_only import (  # noqa: E402
    _create_manual_review_prs,
    finalize_trivy_only_scan,
    run_trivy_only_scan,
)
from rollback_flow import (  # noqa: E402
    _report_rollback_stage,
    _load_rollback_baselines,
    _fetch_live_state,
    _run_rollback_preview,
    _run_rollback,
    _do_run_rollback,
)
from apply_flow import (  # noqa: E402
    _revert_on_gate_failure,
    _pr_requires_terraform,
    _run_apply,
)
from graph_wiring import graph, workflow  # noqa: E402

def _print_drift_exceptions(drift_report_str: str):
    """Display suppressed drift, expired exceptions, and a copy-paste JSON
    snippet for adding new entries to the drift-exceptions registry."""
    try:
        report = json.loads(drift_report_str)
    except (json.JSONDecodeError, ValueError):
        return

    suppressed = report.get("suppressed_resources") or []
    expired = report.get("expired_exceptions") or []

    if expired:
        print(f"\n  !! {len(expired)} drift exception(s) have EXPIRED and are no longer suppressing drift:")
        for exc in expired:
            print(f"    - {exc.get('resource_address', '?')} "
                  f"(drift_type={exc.get('drift_type', '?')}, "
                  f"expired={exc.get('expires', '?')})")
        print()

    if suppressed:
        auto_exc = [r for r in suppressed if r.get("_suppressed_by", {}).get("auto")]
        manual_exc = [r for r in suppressed if not r.get("_suppressed_by", {}).get("auto")]
        if auto_exc:
            print(f"  [suppressed] {len(auto_exc)} drift finding(s) auto-suppressed by drift-exceptions.json:")
            for r in auto_exc:
                exc = r.get("_suppressed_by", {})
                print(f"    - {r.get('address', '?')}  →  {exc.get('reason', '?')[:100]}")
            print()
        if manual_exc:
            print(f"  [noted] {len(manual_exc)} drift finding(s) suppressed by drift-exceptions.json (manual ack):")
            for r in manual_exc:
                exc = r.get("_suppressed_by", {})
                print(f"    - {r.get('address', '?')}  →  {exc.get('reason', '?')[:100]}")
            print()

    resources = report.get("resources") or []
    if resources:
        auto = [r for r in resources if r.get("status") == "auto_suppressed"]
        external = [r for r in resources if r.get("status") == "externally_managed"]
        actionable = [r for r in resources
                      if r not in external and r not in auto]

        if auto:
            print(f"  [suppressed] {len(auto)} resource(s) auto-suppressed "
                  f"(expected drift -- ASG-managed, AWS-managed tags, etc.):")
            for r in auto:
                reasons = r.get("_auto_reasons", [])
                print(f"      {r['address']}  ({'; '.join(reasons[:2])})")
                # Log auto-suppressed events to history for trend visibility.
                try:
                    import drift_history
                    drift_history.append_entry(
                        resource_id=r["address"],
                        account_label=_account_label,
                        region=_region,
                        pr_type="auto_suppressed",
                        severity=r.get("security_impact", "LOW"),
                        fields_changed=[],
                        drift_summary="; ".join(reasons),
                        status="suppressed",
                    )
                except Exception:
                    pass
            print()

        if external:
            print(f"  !! {len(external)} resource(s) have drift covered by lifecycle.ignore_changes "
                  f"-- managed outside Terraform, will not attempt reconciliation:")
            for r in external:
                ignored = r.get("_ignored_fields", [])
                print(f"      {r['address']}  (ignored: {', '.join(ignored)})")
            print()

        if actionable:
            has_security = any(r.get("security_impact") == "high" for r in actionable)
            has_deleted = any(r.get("status") == "deleted_externally" for r in actionable)
            if has_security or has_deleted:
                print(f"  [review] {len(actionable)} drift finding(s) may need human review:")
                for r in actionable:
                    if r.get("security_impact") == "high" or r.get("status") == "deleted_externally":
                        fields = list(r.get("changes", {}).keys())
                        dtype = fields[0] if len(fields) == 1 else "*"
                        print(f"      {r['address']}  →  {r.get('security_impact', '?')} impact")
                        snippet = {
                            "resource_address": r["address"],
                            "drift_type": dtype,
                            "reason": "<why this drift is accepted>",
                            "approved_by": "<your-name>",
                            "approved_date": datetime.now().strftime("%Y-%m-%d"),
                            "expires": datetime.now().replace(year=datetime.now().year + 1).strftime("%Y-%m-%d"),
                        }
                        print(f"      Add to drift-exceptions.json:")
                        print(f"      {json.dumps(snippet, indent=6)}")

                # ── Summary ──
                total_suppressed = len(auto) + len(external) + len(suppressed)
                if total_suppressed:
                    types = []
                    if auto: types.append(f"{len(auto)} auto-suppressed")
                    if external: types.append(f"{len(external)} lifecycle.ignore_changes")
                    if suppressed: types.append(f"{len(suppressed)} drift-exceptions")
                    print(f"  [summary] Suppression summary: {', '.join(types)} -- "
                          f"{len(actionable)} actionable remaining")
                print()


def _current_git_branch(tf_dir: str) -> str:
    """Return the checked-out branch of the git repo at *tf_dir* ('' when
    not a repo / detached HEAD)."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=tf_dir,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""



if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Terraform drift detection and reconciliation agent."
    )
    parser.add_argument(
        "--tf-dir",
        default=None,
        help="Path to the terraform directory to scan for drift (default: resolved from environment)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region for Bedrock LLM calls (default: us-east-1)",
    )
    parser.add_argument(
        "--account-label",
        default=os.environ.get("ACCOUNT_LABEL", "default"),
        help="Human-readable label for the AWS account being scanned",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["drift_only", "drift_and_unmanaged", "unmanaged_only"],
        default="drift_only",
        help="Scan mode: drift_only (default), drift_and_unmanaged, or unmanaged_only",
    )
    parser.add_argument(
        "--trivy-only",
        action="store_true",
        default=False,
        help="Run only the Trivy security scan (no drift detection, no reconcile)",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        default=False,
        help="Roll back a previously merged drift-fix PR",
    )
    parser.add_argument(
        "--rollback-pr",
        type=int,
        default=None,
        help="PR number whose drift fix to roll back (required with --rollback)",
    )
    parser.add_argument(
        "--rollback-preview",
        action="store_true",
        default=False,
        help="Dry-run: show what a rollback would change without patching files or creating a PR",
    )
    parser.add_argument(
        "--apply-pr",
        type=int,
        default=None,
        help="PR number of an approved drift fix to terraform apply (server-side apply trigger)",
    )
    parser.add_argument(
        "--revert-pr",
        type=int,
        default=None,
        help="PR number of a REJECTED drift fix — apply pre-drift main to revert AWS to match code",
    )
    parser.add_argument(
        "--trends",
        action="store_true",
        default=False,
        help="Generate a drift-trends report instead of running the pipeline",
    )
    parser.add_argument(
        "--trends-account",
        default=None,
        help="Account to report on with --trends (default: same as --account-label)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="UUID of the scan_runs row (set by dashboard API, propagated for progress updates)",
    )
    parser.add_argument(
        "--trends-days",
        type=int,
        default=90,
        help="Lookback window in days for --trends (0 = all-time)",
    )
    args = parser.parse_args()

    # Set module-level globals before the pipeline runs so graph nodes
    # (alerts, LLM calls, unmanaged scanner) pick up the right values.
    _region = args.region
    _account_label = args.account_label
    _run_id = args.run_id

    # --trends mode: report only, no terraform directory needed.
    if args.trends:
        import drift_trends
        account = args.trends_account or args.account_label
        report = drift_trends.generate_report(account, days=args.trends_days)
        output_path = os.path.join(os.getcwd(), f"drift-trends-report-{account}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(report)
        print(f"\n[Success] Trends report written to: {output_path}")
        sys.exit(0)

    # --trivy-only mode: security scan only, no drift detection or terraform plan.
    if args.trivy_only:
        if args.tf_dir is not None:
            tf_dir = os.path.abspath(args.tf_dir)
        else:
            import os as _os
            import requests as _requests
            url = _os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
            key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            env_dict = {}
            if url and key:
                resp = _requests.get(
                    f"{url}/rest/v1/environments?select=*&slug=eq.{_account_label}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if resp.status_code == 200 and resp.json():
                    env_dict = resp.json()[0]
            if not env_dict:
                raise RuntimeError(
                    f"No environment found for slug '{_account_label}' — "
                    f"cannot resolve terraform directory."
                )
            from drift_reconciler.environment_credentials import resolve_tf_dir
            tf_dir = resolve_tf_dir(env_dict)

        if not os.path.isdir(tf_dir):
            raise RuntimeError(f"Terraform directory not found: {tf_dir}")

        # Scan freshness: --tf-dir skips resolve_tf_dir's built-in
        # fetch+reset.  Refresh whenever tf_dir lives inside the drift
        # clone base — same guard used for drift scans — so a merged
        # security fix is present before Trivy re-scans.
        _clone_base = os.environ.get(
            "DRIFT_CLONE_BASE",
            os.path.join(os.path.expanduser("~"), ".drift-clones"),
        )
        try:
            _in_clone_base = (
                os.path.commonpath([os.path.abspath(tf_dir), os.path.abspath(_clone_base)])
                == os.path.abspath(_clone_base)
            )
        except ValueError:
            _in_clone_base = False
        if _in_clone_base:
            _branch = _current_git_branch(tf_dir)
            if _branch:
                from drift_reconciler.environment_credentials import refresh_clone
                refresh_clone(tf_dir, _branch, slug=args.account_label)

        try:
            results = run_trivy_only_scan(tf_dir, _account_label, _account_label, _run_id)
            finalize_trivy_only_scan(_run_id, results)
        except Exception as e:
            if _run_id:
                from scan_runs import update_scan_run
                from datetime import datetime as dt, timezone
                update_scan_run(
                    _run_id,
                    status="failed",
                    completed_at=dt.now(timezone.utc).isoformat(),
                    result_summary=str(e),
                )
            raise
        sys.exit(0)

    # Resolve tf_dir once — used by rollback, rollback_preview, and the
    # main drift-reconciliation pipeline below.
    if args.tf_dir is not None:
        tf_dir = os.path.abspath(args.tf_dir)
    else:
        import os as _os
        import requests as _requests
        url = _os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        env_dict = {}
        if url and key:
            resp = _requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{_account_label}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        if not env_dict:
            raise RuntimeError(
                f"No environment found for slug '{_account_label}' — "
                f"cannot resolve terraform directory."
            )
        from drift_reconciler.environment_credentials import resolve_tf_dir
        tf_dir = resolve_tf_dir(env_dict)

    if not os.path.isdir(tf_dir):
        raise RuntimeError(f"Terraform directory not found: {tf_dir}")

    # Scan freshness: an explicit --tf-dir skips resolve_tf_dir's built-in
    # fetch+reset, so a direct/scheduled CLI scan can evaluate a stale
    # clone when new commits were pushed.  Refresh whenever tf_dir lives
    # inside the drift clone base — never touch arbitrary git repos.
    # Already-current clones make this a no-op (fetch finds nothing new).
    _clone_base = os.environ.get(
        "DRIFT_CLONE_BASE",
        os.path.join(os.path.expanduser("~"), ".drift-clones"),
    )
    # commonpath raises on Windows when paths are on different drives
    # (legacy local tf-dir vs clone base) — skip the refresh then.
    try:
        _in_clone_base = (
            os.path.commonpath([os.path.abspath(tf_dir), os.path.abspath(_clone_base)])
            == os.path.abspath(_clone_base)
        )
    except ValueError:
        _in_clone_base = False
    if _in_clone_base:
        _branch = _current_git_branch(tf_dir)
        if _branch:
            from drift_reconciler.environment_credentials import refresh_clone
            refresh_clone(tf_dir, _branch, slug=args.account_label)

    if args.rollback:
        if not args.rollback_pr:
            print("Error: --rollback-pr is required with --rollback")
            sys.exit(1)
        try:
            _run_rollback(tf_dir, args.rollback_pr, run_id=args.run_id)
        except Exception as e:
            # _run_rollback normally updates the DB itself before re-raising.
            # This outer catch is a safety net for exceptions that escape
            # before the DB update (e.g. a NameError at the call site, or a
            # rollback_runs update that failed inside the inner handler).
            print(f"Rollback failed: {e}")
            try:
                from rollback_runs import update_rollback_run
                from datetime import datetime as _dt, timezone as _tz
                update_rollback_run(
                    args.run_id,
                    status="failed",
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    result=humanize_rollback_error(str(e)),
                )
            except Exception:
                pass
            sys.exit(1)
        sys.exit(0)

    if args.rollback_preview:
        if not args.rollback_pr or not args.run_id:
            print("Error: --rollback-pr and --run-id are required with --rollback-preview")
            sys.exit(1)
        try:
            _run_rollback_preview(tf_dir, args.rollback_pr, args.account_label, args.run_id)
        except Exception as e:
            # _run_rollback_preview normally catches its own errors and updates
            # the DB.  This outer catch is a safety net for exceptions that
            # escape — e.g. a NameError from a stale variable reference, or a
            # failed DB update inside the inner except handler.  Without this,
            # the rollback_runs row would stay "running" forever.
            print(f"Rollback preview failed: {e}")
            try:
                from rollback_runs import update_rollback_run
                from datetime import datetime as _dt, timezone as _tz
                update_rollback_run(
                    args.run_id,
                    status="failed",
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    result=humanize_rollback_error(str(e)),
                )
            except Exception:
                pass
            sys.exit(1)
        sys.exit(0)

    if args.apply_pr:
        # Server-side apply of an approved drift fix — _run_apply writes
        # the pending_applies row itself in both success and error paths.
        _run_apply(tf_dir, args.apply_pr, args.account_label, run_id=args.run_id, is_revert=False)
        sys.exit(0)

    if args.revert_pr:
        # Server-side revert of a REJECTED drift fix — the PR was never
        # merged, so origin/main is still pre-drift code; this apply
        # reverts AWS to match code.  Same _run_apply path as approve.
        _run_apply(tf_dir, args.revert_pr, args.account_label, run_id=args.run_id, is_revert=True)
        sys.exit(0)

    try:

        _tf_dir = tf_dir

        scan_unmanaged = args.scan_mode in ("drift_and_unmanaged", "unmanaged_only")

        # Gather the data using our folder-aware pipeline — except in
        # unmanaged_only mode, where drift detection is irrelevant and
        # the terraform plan would only waste time and log a misleading
        # failure on a step we never needed.
        if args.scan_mode == "unmanaged_only":
            print("Skipping terraform plan — unmanaged_only mode does not need drift detection.")
            drift_report = json.dumps({"report_type": "no_drift", "resources": []})
        else:
            drift_report = get_terraform_drift_data(tf_dir, _drift_script_path)

        _terraform_failed = False


        if "Failed" in drift_report or "Error" in drift_report:
            if scan_unmanaged:
                print(f"\n⚠  Terraform plan failed — proceeding with unmanaged scan only.")
                print(_strip_ansi(drift_report))
                drift_report = json.dumps({"report_type": "no_drift", "resources": []})
                _terraform_failed = True
            else:
                raise RuntimeError(f"Terraform pipeline failed:\n{_strip_ansi(drift_report)}")

        _print_drift_exceptions(drift_report)

        if not _terraform_failed:
            print("\nData fetched successfully. Sending to LLM for analysis...")
        system_prompt = (
            f"## Context\n"
            f"Account: {_account_label}  |  Region: {_region}\n\n"
            "## Input Format\n"
            "The input follows this exact JSON structure (provided as raw string):\n"
            "{\n"
            "  \"report_type\": \"drift\"|\"no_drift\"|\"pending_changes\",\n"
            "  \"resources\": [\n"
            "    {\n"
            "      \"address\": \"resource_type.resource_name\",\n"
            "      \"changes\": {\n"
            "        \"field_name\": {\"before\": \"value\", \"after\": \"value\"},\n"
            "        ...\n"
            "      },\n"
            "      \"status\": null|\"deleted_externally\",\n"
            "      \"sensitive\": true|false,\n"
            "      \"security_impact\": null|\"low\"|\"medium\"|\"high\"\n"
            "    },\n"
            "    ...\n"
            "  ],\n"
            "  \"pending_operations\": [\n"
            "    {\"action\": \"create\"|\"delete\", \"address\": \"resource_type.resource_name\"},\n"
            "    ...\n"
            "  ]\n"
            "}\n\n"

            "## Analysis Rules\n"
            "1. Treat ONLY resources in the 'resources' array with 'changes' as actual drift\n"
            "2. 'pending_operations' are informational - never propose changes for these\n"
            "3. For each drifted field, show:\n"
            "   - Change reason (if evident from field patterns)\n"
            "   - Exact HCL modification needed to reconcile\n"
            "   - Security impact level from the report\n"
            "4. Highlight HIGH impact changes with: ⚠️ [SECURITY REVIEW REQUIRED]\n"
            "5. Assume live AWS state is authoritative unless change appears clearly erroneous\n"
            "6. SPECIAL CASE — status == 'deleted_externally': the resource block was REMOVED from\n"
            "   live AWS but still exists in Terraform code/state. Because live AWS state is\n"
            "   authoritative (rule 5), the correct reconciliation is to REMOVE this resource's\n"
            "   block from the .tf file — NOT to re-add or restore it. Phrase the fix as\n"
            "   'Remove resource.<address> from Terraform configuration to match live AWS state'\n"
            "   and never suggest re-adding, restoring, or recreating a deleted_externally resource.\n"
            "7. For findings that include a ``cost_impact`` field, include the estimated\n"
            "   monthly cost in your analysis and flag any resource costing more than\n"
            "   $50/mo with ⚠️ COST WARNING.\n"
            "8. Be concise: at most 2-3 sentences per drifted resource, one short\n"
            "   header line per resource, and no preamble, closing summary, or\n"
            "   restatement of these rules. Output length must stay the same\n"
            "   regardless of which model generates it.\n\n"
        )

        user_query = f"Here is the processed drift report data:\n\n{drift_report}\n\nProvide a plan to resolve this drift."

        initial_state = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query}
            ],
            "trivy_scanned": False,
            "scan_unmanaged": scan_unmanaged,
            "scan_mode": args.scan_mode,
            "run_id": args.run_id,
            "terraform_failed": _terraform_failed,
        }

        _all_findings: list[dict] = []
        _all_pr_urls: list[str] = []
        _pd_alerts_sent = 0
        _slack_messages_sent = 0
        for event in graph.stream(initial_state):
            for node, data in event.items():
                if not data:
                    continue
                findings = data.get("drift_findings") or []
                if findings:
                    _all_findings = findings
                urls = data.get("pr_urls") or []
                if urls:
                    _all_pr_urls = urls
                alerts = data.get("alerts_sent") or {}
                if alerts.get("pagerduty"):
                    _pd_alerts_sent = alerts["pagerduty"]
                if alerts.get("slack"):
                    _slack_messages_sent = alerts["slack"]

        # Mark scan as complete.
        if _run_id:
            from scan_runs import update_scan_run
            from datetime import datetime as dt, timezone
            summary = {}

            # Split findings by origin.
            drift_findings = [f for f in _all_findings if f.get("status") not in unmanaged_scanner.UNMANAGED_STATUSES]
            unmanaged_findings = [f for f in _all_findings if f.get("status") in unmanaged_scanner.UNMANAGED_STATUSES]

            if not _terraform_failed:
                summary["mode"] = "drift_only" if not unmanaged_findings else "full"
            else:
                summary["mode"] = "unmanaged_only"
                summary["notice"] = "Terraform state backend unavailable — only unmanaged resources were scanned. Configuration drift was not checked."
                summary["skipped_stages"] = ["reconcile_agent", "trivy_gate"]

            # Structured blocks per scan type.
            drift_urls = [u["url"] for u in _all_pr_urls if u.get("type") == "drift"]
            unmanaged_urls = [u["url"] for u in _all_pr_urls if u.get("type") == "unmanaged"]
            drift_block = {
                "found": len(drift_findings) > 0,
                "count": len(drift_findings),
                "findings": [{"resource_id": f.get("resource_id", "?"), "risk_level": f.get("risk_level", "LOW")} for f in drift_findings],
                "pr_links": drift_urls,
            }
            unmanaged_block = {
                "found": len(unmanaged_findings) > 0,
                "count": len(unmanaged_findings),
                "findings": [{"resource_id": f.get("resource_id", "?"), "risk_level": f.get("risk_level", "LOW")} for f in unmanaged_findings],
                "pr_links": unmanaged_urls,
            }
            summary["drift"] = drift_block
            summary["unmanaged"] = unmanaged_block
            summary["alerts_sent"] = {"pagerduty": _pd_alerts_sent, "slack": _slack_messages_sent}

            update_scan_run(
                _run_id,
                status="complete",
                completed_at=dt.now(timezone.utc).isoformat(),
                result_summary=summary,
                pr_links=[u["url"] for u in _all_pr_urls] if _all_pr_urls else None,
            )
    except Exception as e:
        if _run_id:
            try:
                from scan_runs import update_scan_run
                from datetime import datetime as dt, timezone
                update_scan_run(
                    _run_id,
                    status="failed",
                    completed_at=dt.now(timezone.utc).isoformat(),
                    result_summary=humanize_terraform_error(str(e)),
                )
            except Exception as se:
                print(f"  [scan_runs] Failed to mark scan as failed: {se}")
        raise
