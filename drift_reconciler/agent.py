import argparse
from datetime import datetime
import logging
import os

# Suppress botocore credential-discovery noise ("Both api_key and AWS
# credentials were provided …") that fires every time a Bedrock client
# is instantiated — twice per run (main agent + Trivy gate).  This is
# purely SDK chatter; actual auth errors still surface as exceptions.
logging.getLogger("botocore").setLevel(logging.ERROR)
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Annotated
from typing_extensions import TypedDict
from langchain_aws import ChatBedrockConverse
from langgraph.graph import StateGraph, START, END
import pagerduty_alert as pga
import slack_notify as slack
from drift_reconciler.environment_credentials import get_aws_session
import github_integration as gi
import json
from langchain_core.messages import AIMessage
from trivy_agent import graph as trivy_graph, State as TrivyState
from trivy_agent import _run_trivy, _extract_issues
from scan_runs import report_stage
import unmanaged_scanner

# Resolved at startup from CLI args (or env fallback).
_account_label = "default"
_region = os.environ.get("AWS_REGION", "us-east-1")
_tf_dir: str | None = None
_run_id: str | None = None

# Derived from this script's location — the drift-formatting script lives
# alongside it in the same directory.
_drift_script_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "formatting_drift_json.py"
)

_llm = None


def _get_llm():
    """Lazily construct the Bedrock LLM client so --region from CLI takes
    effect before the first call."""
    global _llm
    if _llm is None:
        _llm = ChatBedrockConverse(
            model="us.amazon.nova-pro-v1:0",
            temperature=0.1,
            region_name=_region,
        )
    return _llm

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def humanize_terraform_error(raw_error: str) -> dict:
    """Return ``{summary, detail, suggestion}`` for a terraform error."""
    text = raw_error.lower() if raw_error else ""

    patterns = [
        (("nosuchbucket", "does not exist"), {
            "summary": "The Terraform state backend for this scope isn't set up yet.",
            "suggestion": "Confirm the S3 state bucket exists for this account/region before scanning.",
        }),
        (("invalidclienttokenid", "expiredtoken", "unrecognizedclientexception"), {
            "summary": "AWS credentials for this scope are invalid or expired.",
            "suggestion": "Check the IAM credentials/role configured for this scope.",
        }),
        (("accessdenied", "access denied"), {
            "summary": "The configured AWS credentials don't have permission to read this scope's infrastructure.",
            "suggestion": "Check IAM permissions for the scan role.",
        }),
        (("connection refused", "timeout", "could not connect"), {
            "summary": "Couldn't reach AWS or the Terraform backend — possible network issue.",
            "suggestion": "Check network connectivity and try again.",
        }),
        (("profilenotfound", "profile", "could not be found"), {
            "summary": "The AWS profile configured for this environment doesn't exist on this machine.",
            "suggestion": "Create the AWS named profile in ~/.aws/config, or update the environment's profile via the Environments page.",
        }),
    ]

    for keywords, info in patterns:
        if any(kw in text for kw in keywords):
            return {"summary": info["summary"], "detail": raw_error, "suggestion": info["suggestion"]}

    return {
        "summary": "The Terraform plan failed with an unrecognised error.",
        "detail": raw_error,
        "suggestion": "See technical details below.",
    }


# ==========================================
# 1. RUN TERRAFORM & DRIFT SCRIPTS
# ==========================================
def get_terraform_drift_data(tf_dir: str, drift_script_path: str) -> str:
    """Executes CLI commands using the supplied terraform directory and
    drift-formatting script path."""

    if not os.path.exists(tf_dir):
        return f"Error: The Terraform directory '{tf_dir}' does not exist."

    print(f"Step 1: Running 'terraform plan' inside: {tf_dir}...")
    try:
        subprocess.run(
            ["terraform", "plan", "-no-color", "-out=tfplan"],
            cwd=tf_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        return f"Terraform Plan Failed:\n{e.stderr}"

    print("Step 2: Exporting plan to JSON using Native Python...")
    try:
        show_result = subprocess.run(
            ["terraform", "show", "-no-color", "-json", "tfplan"],
            cwd=tf_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        plan_json_path = os.path.join(tf_dir, "plan.json")
        with open(plan_json_path, "w", encoding="utf-8", newline="") as f:
            f.write(show_result.stdout)

    except subprocess.CalledProcessError as e:
        return f"Exporting plan.json Failed:\n{e.stderr}"
    except Exception as e:
        return f"Writing plan.json file failed:\n{str(e)}"

    print("Step 3: Processing drift format script...")
    target_plan_json = os.path.join(tf_dir, "plan.json")

    format_script_cmd = [
        "python",
        drift_script_path,
        target_plan_json,
        "--account", _account_label,
    ]
    try:
        result = subprocess.run(
            format_script_cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Formatting Drift JSON Script Failed:\n{e.stderr}"

# ==========================================
# 2. LANGGRAPH STRUCTURE
# ==========================================
class State(TypedDict):
    messages: Annotated[list, lambda x, y: x + y]
    drift_detected: bool
    drift_findings: list[dict]   # one entry per drifted resource
    trivy_scanned: bool
    scan_unmanaged: bool
    run_id: str | None
    terraform_failed: bool


def map_risk(security_impact) -> str:
    return {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(security_impact, "LOW")


def build_drift_summary(resource: dict) -> str:
    if resource.get("status") == "deleted_externally":
        return "Resource was deleted outside of Terraform (found in state, missing from AWS)."
    if resource.get("status") == "externally_managed":
        ignored = resource.get("_ignored_fields", [])
        return f"Drift on fields covered by lifecycle.ignore_changes ({', '.join(ignored)}) — managed outside Terraform."
    changes = resource.get("changes", {})
    lines = [f"- `{field}`: before `{v.get('before')}` → after `{v.get('after')}`" for field, v in changes.items()]
    return "\n".join(lines)


def build_drift_findings(drift_report_json: dict) -> list[dict]:
    findings = []
    if drift_report_json.get("report_type") != "drift":
        return findings

    for resource in drift_report_json.get("resources", []):
        # deleted_externally / externally_managed resources have no
        # "changes" but are still real findings worth reporting.
        status = resource.get("status")
        if not resource.get("changes") and status not in ("deleted_externally", "externally_managed"):
            continue
        findings.append({
            "resource_id": resource["address"],
            "risk_level": map_risk(resource.get("security_impact")),
            "drift_summary": build_drift_summary(resource),
            "plan_output": json.dumps(resource.get("changes") or {"status": status}, indent=2),
            "file_path": resource.get("file_path"),
            "changes": resource.get("changes", {}),
            "status": status,
        })
    return findings



def agent_node(state: State):
    report_stage(state.get("run_id"), "reconcile_agent")
    raw_report_str = ""
    for msg in state["messages"]:
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if "processed drift report" in content:
            raw_report_str = content
            break

    try:
        json_start = raw_report_str.index("{")
        json_end = raw_report_str.rindex("}") + 1
        drift_report_json = json.loads(raw_report_str[json_start:json_end])
    except (ValueError, json.JSONDecodeError):
        drift_report_json = {"report_type": "unknown", "resources": []}

    drift_detected = drift_report_json.get("report_type") == "drift"

    if not drift_detected:
        # Preserve any unmanaged findings that were already attached
        # by the optional unmanaged-scan node.
        existing = state.get("drift_findings") or []
        return {
            "messages": [AIMessage(content="STATUS: NO_DRIFT\nNo configuration drift detected.")],
            "drift_detected": state.get("drift_detected", False),
            "drift_findings": existing,
        }

    # Strip externally_managed resources from the LLM prompt — the LLM
    # should only see actionable drift it can propose fixes for.
    actionable_resources = [
        r for r in drift_report_json.get("resources", [])
        if r.get("status") != "externally_managed"
    ]
    clean_report = dict(drift_report_json, resources=actionable_resources)
    llm_messages = []
    for msg in state["messages"]:
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if "processed drift report" in content:
            llm_messages.append({"role": msg.get("role", "user"),
                                 "content": content.replace(
                                     raw_report_str[json_start:json_end],
                                     json.dumps(clean_report))})
        else:
            llm_messages.append(msg)

    response = _get_llm().invoke(llm_messages)
    drift_only = build_drift_findings(drift_report_json)
    # Merge any unmanaged findings that were already attached by the
    # optional unmanaged-scan node so they survive the state update.
    existing = state.get("drift_findings") or []
    merged = existing + drift_only
    # Sort so findings with cost impact appear first (highest $ first)
    # — the LLM sees the most expensive untracked resources upfront.
    merged.sort(
        key=lambda f: (f.get("cost_impact") or {}).get("monthly_estimate_usd", -1),
        reverse=True,
    )
    return {
        "messages": [response],
        "drift_detected": True,
        "drift_findings": merged,
    }


TF_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')


def _apply_changes_to_file(file_path: str, resource_addr: str, changes: dict) -> bool:
    """Apply before→after value replacements inside the named resource block.
    Returns True if at least one change was applied."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return False

    if "." not in resource_addr:
        return False
    want_type, want_name = resource_addr.split(".", 1)

    # ponytail: simple line-level find-and-replace inside the resource block.
    # A proper HCL-aware attribute setter would be more robust.
    lines = content.splitlines()
    in_block = False
    depth = 0
    applied = False
    for i, line in enumerate(lines):
        m = TF_RESOURCE_RE.search(line)
        if m and m.group(1) == want_type and m.group(2) == want_name:
            in_block = True
            depth = line.count("{") - line.count("}")
            continue
        if in_block:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
            for field, vals in changes.items():
                before_val = str(vals.get("before", ""))
                after_val = str(vals.get("after", ""))
                if before_val and before_val in line:
                    lines[i] = line.replace(before_val, after_val, 1)
                    applied = True
                    break

    if applied:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError:
            return False
    return applied


def trivy_gate(state: State):
    report_stage(state.get("run_id"), "trivy_gate")
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
    baseline_raw = _run_trivy(src_dir)
    baseline_issues: list[dict] = []
    if "error" not in baseline_raw:
        baseline_issues = _extract_issues(baseline_raw, src_dir)
    print(f"  [trivy-gate] Baseline scan: {len(baseline_issues)} pre-existing issue(s)")

    print(f"  [trivy-gate] Running security scan on proposed drift fixes …")

    try:
        # Copy the terraform directory into the temp workspace so Trivy
        # scans the proposed fix, not the current (pre-drift) code.
        for item in os.listdir(src_dir):
            s = os.path.join(src_dir, item)
            d = os.path.join(tmpdir, item)
            if os.path.isfile(s) and item.endswith(".tf"):
                shutil.copy2(s, d)

        # Apply the proposed after-values to the temp copies.
        for f in actionable:
            tf_file = os.path.join(tmpdir, os.path.basename(f["file_path"]))
            if os.path.isfile(tf_file):
                _apply_changes_to_file(tf_file, f["resource_id"], f["changes"])

        # Invoke the self-contained trivy scan→fix→scan loop.
        trivy_initial: TrivyState = {
            "tf_dir": tmpdir,
            "scan_results": [],
            "issues": [],
            "fixes_applied": [],
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
            if r.get("scope") == _account_label:
                rules[r["severity"]] = r["channel"]
        return rules
    except Exception:
        # Hardcoded fallback — never silently drop alerts.
        return {"HIGH": "pagerduty", "MEDIUM": "slack", "LOW": "slack"}


def drift_alert(state: State):
    report_stage(state.get("run_id"), "drift_alert")
    """Route findings by severity using Supabase routing rules, falling
    back to hardcoded HIGH→PagerDuty / else→Slack if unreachable."""
    if not state.get("drift_detected"):
        return {"messages": [], "alerts_sent": {"pagerduty": 0, "slack": 0}}

    active = [f for f in state["drift_findings"]
              if f.get("status") != "externally_managed"]
    if not active:
        return {"messages": [], "alerts_sent": {"pagerduty": 0, "slack": 0}}

    rules = _load_routing_rules()

    pd_findings = [f for f in active if rules.get(f.get("risk_level", "LOW")) == "pagerduty"]
    slack_findings = [f for f in active if rules.get(f.get("risk_level", "LOW")) == "slack"]

    # PagerDuty → one page per finding.
    pd_sent = 0
    for finding in pd_findings:
        if finding.get("status") in ("unmanaged", "unmanaged_tagged"):
            event_type = "Unmanaged resource"
        else:
            event_type = "Drift detected"
        summary = f"{event_type}: {finding['resource_id']}"
        cost = finding.get("cost_impact")
        if cost:
            summary += f" (${cost['monthly_estimate_usd']:.2f}/mo)"
        result = pga.trigger_pagerduty_alert(
            summary=summary,
            severity="error",
            source="terraform-drift-engine",
            dedup_key=f"drift-{finding['resource_id']}",
            account_label=_account_label,
        )
        if result:  # PagerDuty returns {} on failure, non-empty dict on dispatch
            pd_sent += 1

    # Slack → batched.
    slack_sent = 0
    if slack_findings:
        slack_sent = slack.notify_all(slack_findings, _account_label)

    return {"messages": [], "alerts_sent": {"pagerduty": pd_sent, "slack": slack_sent}}
def drift_pr_from_finding(state: State):
    report_stage(state.get("run_id"), "drift_pr")
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

    pr_urls = []
    for file_path, group in by_file.items():
        if len(group) == 1:
            pr = gi.create_drift_pr_for_mode(group[0], "code_to_reality", account_label=_account_label)
        else:
            pr = gi.create_drift_pr_for_file(group, "code_to_reality", account_label=_account_label)
        if pr is not None:
            # Findings with file_path are always drift fixes (unmanaged
            # findings have file_path=None and land in report_only).
            pr_urls.append({"url": pr.html_url, "type": "drift"})

    for finding in report_only:
        pr = gi.create_drift_pr_for_mode(finding, "code_to_reality", account_label=_account_label)
        if pr is not None:
            is_unmanaged = finding.get("status") in ("unmanaged", "unmanaged_tagged")
            pr_urls.append({"url": pr.html_url, "type": "unmanaged" if is_unmanaged else "drift"})

    return {"pr_urls": pr_urls}


def unmanaged_scan_node(state: State):
    report_stage(state.get("run_id"), "unmanaged_scan")
    """Enumerate live AWS resources, subtract what Terraform manages.

    Runs before the reconcile agent when --scan-unmanaged is set.
    Findings are appended to drift_findings so the existing alert/PR
    nodes pick them up without changes."""
    if _tf_dir is None:
        return {"messages": []}

    print("\n--- Unmanaged resource scan ---")
    try:
        # Resolve environment row and build AWS session.
        import os as _os
        import requests as _requests
        env_dict = {}
        url = _os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if url and key:
            resp = _requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{_account_label}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        if not env_dict:
            raise RuntimeError(f"No environment found for slug '{_account_label}' — check the environments table.")
        session = get_aws_session(env_dict)
        live = unmanaged_scanner.scan_unmanaged_resources(session, _region)
    except Exception as e:
        raise

    if not live:
        print("  (no live resources found)")
        return {"messages": []}

    managed = unmanaged_scanner.load_managed_resources(_tf_dir)
    findings = unmanaged_scanner.diff_unmanaged(live, managed, region=_region, tf_dir=_tf_dir, scope=_account_label)

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


workflow = StateGraph(State)
workflow.add_node("unmanaged_scan", unmanaged_scan_node)
workflow.add_node("reconcile_agent", agent_node)
workflow.add_node("trivy_gate", trivy_gate)
workflow.add_node("alert_agent", drift_alert)
workflow.add_node("drift_pr", drift_pr_from_finding)

workflow.add_conditional_edges(
    START,
    lambda state: "unmanaged_scan" if state.get("scan_unmanaged") else "reconcile_agent",
    {"unmanaged_scan": "unmanaged_scan", "reconcile_agent": "reconcile_agent"},
)
workflow.add_edge("unmanaged_scan", "reconcile_agent")
workflow.add_edge("reconcile_agent", "trivy_gate")
workflow.add_edge("trivy_gate", "alert_agent")
workflow.add_edge("trivy_gate", "drift_pr")
workflow.add_edge("alert_agent", END)
workflow.add_edge("drift_pr", END)

graph = workflow.compile()


# ==========================================
# 4. EXECUTION FLOW
# ==========================================
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
        print(f"\n  ⚠ {len(expired)} drift exception(s) have EXPIRED and are no longer suppressing drift:")
        for exc in expired:
            print(f"    - {exc.get('resource_address', '?')} "
                  f"(drift_type={exc.get('drift_type', '?')}, "
                  f"expired={exc.get('expires', '?')})")
        print()

    if suppressed:
        auto_exc = [r for r in suppressed if r.get("_suppressed_by", {}).get("auto")]
        manual_exc = [r for r in suppressed if not r.get("_suppressed_by", {}).get("auto")]
        if auto_exc:
            print(f"  🔇 {len(auto_exc)} drift finding(s) auto-suppressed by drift-exceptions.json:")
            for r in auto_exc:
                exc = r.get("_suppressed_by", {})
                print(f"    - {r.get('address', '?')}  →  {exc.get('reason', '?')[:100]}")
            print()
        if manual_exc:
            print(f"  📋 {len(manual_exc)} drift finding(s) suppressed by drift-exceptions.json (manual ack):")
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
            print(f"  🔇 {len(auto)} resource(s) auto-suppressed "
                  f"(expected drift — ASG-managed, AWS-managed tags, etc.):")
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
            print(f"  ⚠ {len(external)} resource(s) have drift covered by lifecycle.ignore_changes "
                  f"— managed outside Terraform, will not attempt reconciliation:")
            for r in external:
                ignored = r.get("_ignored_fields", [])
                print(f"      {r['address']}  (ignored: {', '.join(ignored)})")
            print()

        if actionable:
            has_security = any(r.get("security_impact") == "high" for r in actionable)
            has_deleted = any(r.get("status") == "deleted_externally" for r in actionable)
            if has_security or has_deleted:
                print(f"  🔍 {len(actionable)} drift finding(s) may need human review:")
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
                    print(f"  📊 Suppression summary: {', '.join(types)} — "
                          f"{len(actionable)} actionable remaining")
                print()


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


def _fetch_live_state(tf_dir: str, resource_id: str, fields: list[str]) -> tuple[str, dict[str, str]]:
    """Run terraform plan in *tf_dir* and extract live field values for
    *resource_id* from the plan JSON.  Returns (outcome, live_values)
    where outcome is ``"present"``, ``"no_diff"``, or ``"not_found"``."""
    try:
        plan_result = subprocess.run(
            ["terraform", "plan", "-no-color", "-out=tfplan", "-input=false", "-lock-timeout=30s"],
            cwd=tf_dir,
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

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        diff: list[dict] = []

        for baseline in baselines:
            resource_id = baseline["resource_id"]
            original_changes = baseline["changes"]
            rel_path = baseline.get("file_path", "")
            file_path = os.path.join(repo_root, rel_path) if rel_path else ""
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
                outcome, live_values = _fetch_live_state(tf_dir, resource_id, fields)
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
        update_rollback_run(
            run_id,
            status="failed",
            completed_at=dt.now(timezone.utc).isoformat(),
            result={"error": str(e)},
        )
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
                update_rollback_run(run_id, status="failed", completed_at=dt.now(timezone.utc).isoformat(), result={"error": str(e)})
            except Exception:
                pass
        raise


def _do_run_rollback(tf_dir: str, pr_number: int, run_id: str | None) -> None:
    """Inner implementation — wrapped by _run_rollback for error handling."""
    _report_rollback_stage(run_id, "loading_baseline")
    baselines = _load_rollback_baselines(pr_number, _account_label)
    if not baselines:
        raise RuntimeError(f"No baselines found in Supabase for PR #{pr_number} ({_account_label})")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"\n--- Rollback checkpoint 1: {len(baselines)} resource(s) in PR #{pr_number} ---\n")

    rollback_ready: list[dict] = []
    for baseline in baselines:
        resource_id = baseline["resource_id"]
        original_changes = baseline["changes"]
        rel_path = baseline.get("file_path", "")
        file_path = os.path.join(repo_root, rel_path) if rel_path else ""
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
            outcome, live_values = _fetch_live_state(tf_dir, resource_id, fields)
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
            account_label=_account_label,
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
        "--scan-unmanaged",
        action="store_true",
        default=False,
        help="Scan for AWS resources that exist outside of Terraform state",
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

    if args.rollback:
        if not args.rollback_pr:
            print("Error: --rollback-pr is required with --rollback")
            sys.exit(1)
        try:
            _run_rollback(tf_dir, args.rollback_pr, run_id=args.run_id)
        except Exception as e:
            print(f"Rollback failed: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.rollback_preview:
        if not args.rollback_pr or not args.run_id:
            print("Error: --rollback-pr and --run-id are required with --rollback-preview")
            sys.exit(1)
        _run_rollback_preview(tf_dir, args.rollback_pr, args.account_label, args.run_id)
        sys.exit(0)

    try:
        # Resolve tf_dir: explicit override, or derived from environment.
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

        _tf_dir = tf_dir

        # Gather the data using our folder-aware pipeline
        drift_report = get_terraform_drift_data(tf_dir, _drift_script_path)

        _terraform_failed = False

        if "Failed" in drift_report or "Error" in drift_report:
            if args.scan_unmanaged:
                print(f"\n⚠  Terraform plan failed — proceeding with unmanaged scan only.")
                print(_strip_ansi(drift_report))
                drift_report = json.dumps({"report_type": "no_drift", "resources": []})
                _terraform_failed = True
            else:
                raise RuntimeError(f"Terraform pipeline failed:\n{_strip_ansi(drift_report)}")

        _print_drift_exceptions(drift_report)

        if not _terraform_failed:
            print("\nData fetched successfully. Sending to Amazon Nova...")
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
            "   $50/mo with ⚠️ COST WARNING.\n\n"
        )

        user_query = f"Here is the processed drift report data:\n\n{drift_report}\n\nProvide a plan to resolve this drift."

        initial_state = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query}
            ],
            "trivy_scanned": False,
            "scan_unmanaged": args.scan_unmanaged,
            "run_id": args.run_id,
            "terraform_failed": _terraform_failed,
        }

        agent_output = ""
        _all_findings: list[dict] = []
        _all_pr_urls: list[str] = []
        _pd_alerts_sent = 0
        _slack_messages_sent = 0
        for event in graph.stream(initial_state):
            for node, data in event.items():
                if not data:
                    continue
                messages = data.get("messages") or []
                if messages:
                    agent_output = messages[-1].content
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

        # Print out to the terminal as usual
        print(f"\n[Agent Response]:\n{agent_output}")

        # Mark scan as complete.
        if _run_id:
            from scan_runs import update_scan_run
            from datetime import datetime as dt, timezone
            summary = {}

            # Split findings by origin.
            drift_findings = [f for f in _all_findings if f.get("status") not in ("unmanaged", "unmanaged_tagged")]
            unmanaged_findings = [f for f in _all_findings if f.get("status") in ("unmanaged", "unmanaged_tagged")]

            if not _terraform_failed:
                summary["mode"] = "drift_only" if not unmanaged_findings else "full"
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", _account_label)
                report_filename = f"drift_reconciliation_report_{safe_label}_{timestamp}.md"
                report_path = os.path.join(tf_dir, report_filename)
                try:
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(f"# Terraform Drift Reconciliation Report\n")
                        f.write(f"**Account:** {_account_label}  \n")
                        f.write(f"**Region:** {_region}  \n")
                        f.write(f"**Generated on:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                        f.write("## Amazon Nova Pro Analysis & Action Plan\n\n")
                        f.write(agent_output)
                    print(f"\n[Success] Report successfully written to: {report_path}")
                    summary["report_path"] = report_path
                except Exception as e:
                    print(f"\n[Warning] Failed to write report file: {str(e)}")
                    summary["report_path"] = f"(write failed: {e})"
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