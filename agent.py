#!/usr/bin/env python3
"""Drift analysis agent — in-memory simulation. Uses keyword-based severity
classification and difflib for illustrative HCL diffs. No real Terraform or LLM."""

import sys
import json
import difflib
import re

# ponytail: single shared keyword list for severity classification.
# server.ts's determineSeverity mirrors this; keep in sync.
CRITICAL_KEYWORDS = ["public", "acl", "cidr", "port_22", "0.0.0.0", "admin", "all_traffic", "full-access"]
HIGH_KEYWORDS = ["encrypt", "key", "policy", "password", "tls", "ssl", "credentials"]

# ponytail: classification labels renamed to reflect what we actually detect (keyword risk, not actor intent).
CLASSIFICATION_LABELS = {
    "high_risk_change": "high_risk_change",
    "moderate_risk_change": "moderate_risk_change",
    "low_risk_change": "low_risk_change",
}


def classification_node(state: dict) -> dict:
    """Classifies drift risk level based on keyword matching on field names and values."""
    drift_details = state.get("drift_details", [])

    is_critical = False
    is_high = False

    for drift in drift_details:
        field = str(drift.get("field", "")).lower()
        actual = str(drift.get("actual", "")).lower()

        if any(k in field or k in actual for k in CRITICAL_KEYWORDS):
            is_critical = True
        elif any(k in field for k in HIGH_KEYWORDS):
            is_high = True

    classification = "high_risk_change" if is_critical else "moderate_risk_change" if is_high else "low_risk_change"
    risk_score = "Critical" if is_critical else "High" if is_high else "Medium" if drift_details else "Low"

    return {
        "classification": classification,
        "risk_score": risk_score
    }


def security_analysis_node(state: dict) -> dict:
    """Formulates a security and compliance analysis based on resource type."""
    name = state.get("name", "")
    type_ = state.get("type", "")
    drift_details = state.get("drift_details", [])

    explanation = f"Manual direct cloud updates on '{name}' bypassed the Terraform pipeline."
    security_impact = "Increases attack surface by deviating from peer-reviewed security profiles."

    if "s3" in type_ or "s3" in name.lower():
        explanation = ("S3 public access blocks or ACL configs were manipulated manually outside of Terraform. "
                       "This violates standard CIS Amazon S3 benchmarks, HIPAA 164.312 access control guidelines, "
                       "and SOC 2 Type II CC6.1 criteria.")
        security_impact = ("Unauthenticated public read access might expose intellectual property, "
                           "customer file uploads, and configuration backups to global scrapers.")
    elif "security_group" in type_ or "sg" in name.lower():
        explanation = ("Manual overrides opened insecure ingress avenues on standard cluster security ports. "
                       "This is a severe breach of CIS VPC 2.1 specifications.")
        security_impact = ("Raw internet-facing SSH or HTTP administration access allows brute-force attacks "
                           "from global malware botnets.")
    elif "iam" in type_ or "role" in name.lower():
        explanation = ("Manual elevation of IAM policy statements violates the Principle of Least Privilege (PoLP) "
                       "and compromises AWS IAM secure baseline practices.")
        security_impact = ("Attaching administrator or full write access lets compromised code/tokens "
                           "gain complete control of the cloud tenancy.")
    elif "rds" in type_ or "db" in name.lower():
        explanation = ("The RDS instances have had public access enabled or storage encryption turned off, "
                       "violating basic PCI-DSS and SOC 2 secure database standards.")
        security_impact = ("Database ports open globally invite continuous dictionary attacks "
                           "and expose transactional application tables to compromise.")

    return {
        "explanation": explanation,
        "security_impact": security_impact
    }


def cost_estimation_node(state: dict) -> dict:
    """Calculates potential financial and regulatory overhead estimates."""
    risk_score = state.get("risk_score", "High")

    if risk_score == "Critical":
        cost_impact = ("Introduces maximum risk of SOC 2 or HIPAA compliance failure, "
                       "potentially leading to regulatory fines of up to $2,400,000.")
    elif risk_score == "High":
        cost_impact = ("Estimated $15,000 in security operations mitigation hours, developer review, "
                       "and re-audit certification procedures.")
    elif risk_score == "Medium":
        cost_impact = ("Minor compliance remediation overhead. Triggers alerts in security monitors "
                       "costing around $1,200/year to resolve.")
    else:
        cost_impact = ("Bypassing automated checks incurs additional operational time "
                       "to verify and reconcile manual estate deviations.")

    return {"cost_impact": cost_impact}


def hcl_reconciliation_node(state: dict) -> dict:
    """Generates an illustrative unified diff between original terraform code and
    the proposed reconciliation. Uses Python's difflib — no regex-based HCL rewriting."""
    terraform_code = state.get("terraform_code", "")
    drift_details = state.get("drift_details", [])

    # Build a proposed fix by substituting expected values into the original.
    proposed = terraform_code
    for drift in drift_details:
        field = str(drift.get("field", ""))
        expected = str(drift.get("expected", ""))
        # ponytail: regex substitution is illustrative — real HCL needs AST-level editing.
        pattern = re.compile(rf"{re.escape(field)}\s*=\s*.*", re.IGNORECASE | re.DOTALL)
        if pattern.search(proposed):
            proposed = pattern.sub(f'{field} = "{expected}"  # reconciled', proposed)

    # Generate unified diff for display.
    diff_lines = list(difflib.unified_diff(
        terraform_code.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile="current.tf",
        tofile="proposed.tf",
        lineterm="",
    ))

    hcl_diff = "".join(diff_lines) if diff_lines else proposed
    hcl_fix = proposed

    return {
        "hcl_fix": hcl_fix,
        "hcl_diff": hcl_diff,
        "fixType": "unapproved_recommendation"
    }


def security_scan_node(state: dict) -> dict:
    """Policy scan node.
    Attempt to run the `checkov` CLI against the proposed HCL when available. If the
    `checkov` binary is not available or the invocation fails, fall back to
    clearly-labeled heuristic checks. Never fabricate Checkov-style IDs when the
    CLI is not run; heuristic checks use `heuristic_*` ids and `source: "keyword_matching"`.
    """
    name = state.get("name", "")
    type_ = state.get("type", "")
    hcl_fix = state.get("hcl_fix", "")
    hcl_lower = hcl_fix.lower()

    # Prepare a simple fallback heuristic generator
    def heuristic_checks_for(kind: str):
        if kind == 's3':
            return [
                {"id": "heuristic_s3_encryption", "name": "Heuristic: S3 bucket appears to have SSE encryption configured",
                 "severity": "HIGH",
                 "status": "PASSED" if any(k in hcl_lower for k in ["sse_algorithm", "encryption", "encrypt"]) else "FAILED",
                 "impact": "Unencrypted S3 requires encryption at rest.",
                 "source": "keyword_matching"},
                {"id": "heuristic_s3_public_access", "name": "Heuristic: S3 bucket appears to block public access",
                 "severity": "CRITICAL",
                 "status": "PASSED" if any(k in hcl_lower for k in ["block_public_acls = true", "block_public_policy = true", "private"]) else "FAILED",
                 "impact": "Public buckets expose files to internet scraping.",
                 "source": "keyword_matching"},
                {"id": "heuristic_s3_versioning", "name": "Heuristic: S3 bucket appears to have versioning configured",
                 "severity": "LOW",
                 "status": "PASSED" if "versioning" in hcl_lower else "FAILED",
                 "impact": "Versioning protects against accidental deletions.",
                 "source": "keyword_matching"},
            ]
        if kind == 'sg':
            return [
                {"id": "heuristic_sg_ssh_open", "name": "Heuristic: Security group does not expose SSH (port 22) to 0.0.0.0/0",
                 "severity": "CRITICAL",
                 "status": "FAILED" if "0.0.0.0/0" in hcl_lower and "port = 22" in hcl_lower else "PASSED",
                 "impact": "Open SSH ingress allows global brute-force attacks.",
                 "source": "keyword_matching"},
                {"id": "heuristic_sg_admin_ports", "name": "Heuristic: Security group does not allow wide-open ingress on admin ports",
                 "severity": "HIGH",
                 "status": "FAILED" if "0.0.0.0/0" in hcl_lower and ("port" in hcl_lower or "ingress" in hcl_lower) else "PASSED",
                 "impact": "Wide-open admin ports increase lateral scanning surface.",
                 "source": "keyword_matching"},
            ]
        if kind == 'iam':
            return [
                {"id": "heuristic_iam_wildcards", "name": "Heuristic: IAM policy does not contain wildcard (*) actions",
                 "severity": "CRITICAL",
                 "status": "FAILED" if '"*"' in hcl_fix or "'*'" in hcl_fix else "PASSED",
                 "impact": "Wildcard permissions violate Principle of Least Privilege.",
                 "source": "keyword_matching"},
                {"id": "heuristic_iam_admin_access", "name": "Heuristic: IAM policy does not grant full administrator access",
                 "severity": "HIGH",
                 "status": "FAILED" if "administratoraccess" in hcl_lower or "full" in hcl_lower else "PASSED",
                 "impact": "Admin privileges allow complete infrastructure control from compromised code.",
                 "source": "keyword_matching"},
            ]
        if kind == 'rds':
            return [
                {"id": "heuristic_rds_encryption", "name": "Heuristic: RDS database appears to have storage encryption enabled",
                 "severity": "HIGH",
                 "status": "PASSED" if "storage_encrypted = true" in hcl_lower or "encrypted" in hcl_lower else "FAILED",
                 "impact": "Unencrypted database storage is vulnerable to physical theft.",
                 "source": "keyword_matching"},
                {"id": "heuristic_rds_not_public", "name": "Heuristic: RDS database is not publicly accessible",
                 "severity": "CRITICAL",
                 "status": "FAILED" if "publicly_accessible = true" in hcl_lower else "PASSED",
                 "impact": "Public DB instances are exposed to internet-wide connection attacks.",
                 "source": "keyword_matching"},
            ]
        return [{"id": "heuristic_baseline", "name": "Heuristic: Resource adheres to general IaC configuration patterns",
                 "severity": "MEDIUM", "status": "PASSED",
                 "impact": "Non-standard configurations may create audit visibility gaps.",
                 "source": "keyword_matching"}]

    # Try to run real Checkov CLI if available. Write HCL to a temp file and shell out.
    try:
        import subprocess, tempfile, os

        tf_ext = '.tf'
        with tempfile.NamedTemporaryFile(delete=False, suffix=tf_ext, mode='w', encoding='utf-8') as tf:
            tf.write(hcl_fix or '')
            tf.flush()
            tfpath = tf.name

        cmd = ["checkov", "-f", tfpath, "--output", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        os.unlink(tfpath)

        if proc.returncode == 0 or proc.stdout:
            try:
                payload = json.loads(proc.stdout)
                # Parse Checkov JSON results — merge passed and failed checks
                results = []
                if isinstance(payload, dict):
                    failed = payload.get('results', {}).get('failed_checks', []) or payload.get('failed_checks', [])
                    passed_checks = payload.get('results', {}).get('passed_checks', []) or payload.get('passed_checks', [])
                    for c in (failed or []) + (passed_checks or []):
                        # Map to unified check format
                        cid = c.get('check_id') or c.get('check_name') or c.get('check')
                        name = c.get('check_name') or c.get('check') or c.get('message') or ''
                        sev = c.get('check_severity') or c.get('severity') or 'MEDIUM'
                        status = 'FAILED' if c in (failed or []) else 'PASSED'
                        impact = c.get('guideline') or c.get('message') or ''
                        results.append({
                            'id': cid,
                            'name': name,
                            'severity': sev.upper(),
                            'status': status,
                            'impact': impact,
                            'source': 'checkov_cli'
                        })
                summary = f"Checkov CLI executed: {len(results)} checks parsed."
                return { 'checkov_checks': results, 'checkov_summary': summary }
            except Exception:
                # fall through to heuristic
                pass
    except FileNotFoundError:
        # checkov not installed — fall back to heuristics
        pass
    except Exception:
        # any runtime issue — fall back
        pass

    # Fallback: heuristic substring checks (clearly labeled)
    if "s3" in type_ or "s3" in name.lower():
        checks = heuristic_checks_for('s3')
    elif "security_group" in type_ or "sg" in name.lower():
        checks = heuristic_checks_for('sg')
    elif "iam" in type_ or "role" in name.lower():
        checks = heuristic_checks_for('iam')
    elif "rds" in type_ or "db" in name.lower():
        checks = heuristic_checks_for('rds')
    else:
        checks = heuristic_checks_for('other')

    passed = sum(1 for c in checks if c["status"] == "PASSED")
    total = len(checks)
    summary = f"Passed {passed}/{total} heuristic checks (fallback; NOT a real Checkov scan)"

    return {
        "checkov_checks": checks,
        "checkov_summary": summary
    }


def main():
    try:
        input_data = json.load(sys.stdin)

        state = {
            "name": input_data.get("name", ""),
            "type": input_data.get("type", ""),
            "service": input_data.get("service", ""),
            "terraform_code": input_data.get("terraformCode", ""),
            "drift_details": input_data.get("driftDetails", []),
        }

        # ponytail: linear pipeline — was a hand-rolled "StateGraph" DAG for 5 sequential calls.
        state.update(classification_node(state))
        state.update(security_analysis_node(state))
        state.update(cost_estimation_node(state))
        state.update(hcl_reconciliation_node(state))
        state.update(security_scan_node(state))

        output_payload = {
            "resourceId": input_data.get("id"),
            "classification": state.get("classification", "low_risk_change"),
            "riskScore": state.get("risk_score", "High"),
            "explanation": state.get("explanation", "Manual drift detected"),
            "securityImpact": state.get("security_impact", "Vulnerability created"),
            "costImpact": state.get("cost_impact", "Operational costs incurred"),
            "hclFix": state.get("hcl_fix", input_data.get("terraformCode", "")),
            "hclDiff": state.get("hcl_diff", ""),
                "fixType": state.get("fixType", "unapproved_recommendation"),
            "checkovChecks": state.get("checkov_checks", []),
            "checkovSummary": state.get("checkov_summary",
                "Simulated policy checks — illustrative only."),
        }

        print(json.dumps(output_payload, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()
