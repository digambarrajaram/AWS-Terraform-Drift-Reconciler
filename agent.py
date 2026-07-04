#!/usr/bin/env python3
"""Drift analysis agent — deterministic keyword pipeline.
Uses the shared module agent_common for all logic."""

import sys
import json
from agent_common import classify_resource, analyse_security, estimate_cost, generate_hcl_fix, run_policy_scan

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

        state.update(classify_resource(state))
        state.update(analyse_security(state))
        state.update(estimate_cost(state))
        state.update(generate_hcl_fix(state))
        state.update(run_policy_scan(state))

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
