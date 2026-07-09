from datetime import datetime
import os
import subprocess
import sys
from typing import Annotated
from typing_extensions import TypedDict
from langchain_aws import ChatBedrockConverse
from langgraph.graph import StateGraph, START, END
import pagerduty_alert as pga
import github_integration as gi
import json

# ==========================================
# CONFIGURATION: SET YOUR PATHS HERE
# ==========================================
# Path to the folder containing your .tf files (e.g., main.tf)
TERRAFORM_DIR = r"D:\aws-terraform-drift-reconciler\test\ec2_terraform" 

# Absolute path to your formatting script
DRIFT_SCRIPT_PATH = r"D:\aws-terraform-drift-reconciler\test\formatting_drift_json.py"

# ==========================================
# 1. RUN TERRAFORM & DRIFT SCRIPTS
# ==========================================
def get_terraform_drift_data() -> str:
    """Executes CLI commands using explicit folder paths."""
    
    if not os.path.exists(TERRAFORM_DIR):
        return f"Error: The Terraform directory '{TERRAFORM_DIR}' does not exist."

    print(f"Step 1: Running 'terraform plan' inside: {TERRAFORM_DIR}...")
    try:
        subprocess.run(
            ["terraform", "plan", "-out=tfplan"], 
            cwd=TERRAFORM_DIR, 
            check=True, 
            capture_output=True, 
            text=True,
            shell=True  # Added shell=True to safely locate the terraform binary
        )
    except subprocess.CalledProcessError as e:
        return f"Terraform Plan Failed:\n{e.stderr}"

    print("Step 2: Exporting plan to JSON using Native Python...")
    try:
        # Run the show command and grab the output string directly
        show_result = subprocess.run(
            ["terraform", "show", "-json", "tfplan"], 
            cwd=TERRAFORM_DIR, 
            check=True, 
            capture_output=True, 
            text=True,
            shell=True
        )
        
        # Write the JSON data exactly as requested (-NoNewline, UTF-8) using Python
        plan_json_path = os.path.join(TERRAFORM_DIR, "plan.json")
        with open(plan_json_path, "w", encoding="utf-8", newline="") as f:
            f.write(show_result.stdout)
            
    except subprocess.CalledProcessError as e:
        return f"Exporting plan.json Failed:\n{e.stderr}"
    except Exception as e:
        return f"Writing plan.json file failed:\n{str(e)}"

    print("Step 3: Processing drift format script...")
    target_plan_json = os.path.join(TERRAFORM_DIR, "plan.json")
    
    format_script_cmd = [
        "python",
        DRIFT_SCRIPT_PATH,
        target_plan_json
    ]
    try:
        result = subprocess.run(
            format_script_cmd, 
            check=True, 
            capture_output=True, 
            text=True,
            shell=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Formatting Drift JSON Script Failed:\n{e.stderr}"

# ==========================================
# 2. INITIALIZE AMAZON NOVA MODEL
# ==========================================
llm = ChatBedrockConverse(
    model="amazon.nova-pro-v1:0", 
    temperature=0.1,
    region_name=os.environ.get("AWS_REGION", "us-east-1")
)


# ==========================================
# 3. DEFINE LANGGRAPH STRUCTURE
# ==========================================
class State(TypedDict):
    messages: Annotated[list, lambda x, y: x + y]
    drift_detected: bool
    drift_findings: list[dict]   # one entry per drifted resource


def map_risk(security_impact) -> str:
    return {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(security_impact, "LOW")


def build_drift_summary(changes: dict) -> str:
    lines = []
    for field, vals in changes.items():
        lines.append(f"- `{field}`: before `{vals.get('before')}` → after `{vals.get('after')}`")
    return "\n".join(lines)


def build_drift_findings(drift_report_json: dict) -> list[dict]:
    """Source of truth for PR content — built straight from the terraform plan JSON,
    not from LLM prose."""
    findings = []
    if drift_report_json.get("report_type") != "drift":
        return findings

    for resource in drift_report_json.get("resources", []):
        changes = resource.get("changes")
        if not changes:
            continue
        findings.append({
            "resource_id": resource["address"],
            "risk_level": map_risk(resource.get("security_impact")),
            "drift_summary": build_drift_summary(changes),
            "plan_output": json.dumps(changes, indent=2),  # or slice raw plan text for this resource
            "file_path": None,   # see note below — you don't have this mapping yet
            "file_content": None,  # see note below
        })
    return findings


def agent_node(state: State):
    response = llm.invoke(state["messages"])

    # Find the raw JSON drift report embedded in the user message (not LLM output)
    raw_report_str = ""
    for msg in state["messages"]:
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if "processed drift report" in content:
            raw_report_str = content
            break

    # Extract just the JSON portion and parse it — this is your ground truth
    try:
        json_start = raw_report_str.index("{")
        json_end = raw_report_str.rindex("}") + 1
        drift_report_json = json.loads(raw_report_str[json_start:json_end])
    except (ValueError, json.JSONDecodeError):
        drift_report_json = {"report_type": "unknown", "resources": []}

    drift_detected = drift_report_json.get("report_type") == "drift"
    findings = build_drift_findings(drift_report_json) if drift_detected else []

    return {
        "messages": [response],
        "drift_detected": drift_detected,
        "drift_findings": findings,
    }
def drift_alert(state: State):
    if not state["drift_detected"]:
        return {"messages": []}
    pga.trigger_pagerduty_alert(
        summary="Terraform Drift Engine Alert - Drift detected in environment",
        severity="error",
        source="terraform-drift-engine"
    )
    return {"messages": []}

def drift_pr_from_finding(state: State):
    if not state["drift_detected"]:
        return {"pr_urls": []}
    pr_urls = []
    for finding in state["drift_findings"]:
        pr = gi.create_drift_pr(
            resource_id=finding["resource_id"],
            pr_title=f"Drift fix: {finding['resource_id']} [{finding['risk_level']}]",
            drift_summary=finding["drift_summary"],
            plan_output=finding["plan_output"],
            file_path=f"drift-reports/{finding['resource_id'].replace('.', '-')}.md",
            file_content=f"# Drift report: {finding['resource_id']}\n\n{finding['drift_summary']}\n\n```\n{finding['plan_output']}\n```",
            risk_level=finding["risk_level"],
        )
        pr_urls.append(pr.html_url)
    return {"pr_urls": pr_urls}


workflow = StateGraph(State)
workflow.add_node("reconcile_agent", agent_node)
workflow.add_node("alert_agent", drift_alert)
workflow.add_node("drift_pr", drift_pr_from_finding)

workflow.add_edge(START, "reconcile_agent")
workflow.add_edge("reconcile_agent", "alert_agent")
workflow.add_edge("reconcile_agent", "drift_pr")
workflow.add_edge("alert_agent", END)
workflow.add_edge("drift_pr", END)

graph = workflow.compile()


# ==========================================
# 4. EXECUTION FLOW
# ==========================================
if __name__ == "__main__":
    # Gather the data using our folder-aware pipeline
    drift_report = get_terraform_drift_data()
    
    if "Failed" in drift_report or "Error" in drift_report:
        print("\nPipeline stopped due to errors:")
        print(drift_report)
        sys.exit(1)

    print("\nData fetched successfully. Sending to Amazon Nova...")
    system_prompt = (
        "You are an expert Cloud DevOps Engineer analyzing a Terraform drift report "
        "generated by our automated pipeline.\n\n"

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
        "5. Assume live AWS state is authoritative unless change appears clearly erroneous\n\n"

        "## Output Format Requirements\n"
        "FIRST LINE MUST BE EXACTLY ONE OF:\n"
        "- STATUS: NO_DRIFT (when report_type is 'no_drift')\n"
        "- STATUS: DRIFT_DETECTED (when resources[] has items with changes)\n"
        "- STATUS: PENDING_CHANGES (when only pending_operations exist)\n\n"

        "For NO_DRIFT:\n"
        "- One concise paragraph confirming no configuration drift\n"
        "- Optionally note any pending operations in one sentence\n\n"

        "For DRIFT_DETECTED:\n"
        "1. Begin with summary table:\n"
        "   | Resource | Changed Fields | Security Impact |\n"
        "   |----------|----------------|------------------|\n"
        "   | ...      | ...            | ...              |\n"
        "2. Per-resource sections with:\n"
        "   - Resource address header\n"
        "   - Change explanation\n"
        "   - HCL code block showing EXACT modifications\n"
        "3. Final verification steps:\n"
        "   - Recommended 'terraform plan' command\n"
        "   - For security-sensitive changes: explicit review instructions\n\n"

        "Important Notes:\n"
        "- NEVER suggest changes beyond those in the provided JSON\n"
        "- Flag ALL medium/high security impact fields with appropriate warnings\n"
        "- Assume this is part of CI/CD pipeline - keep output machine-parseable\n"
        "- Include pipeline step references like [DRIFT-ANALYSIS-01] where appropriate"
    )

    
    user_query = f"Here is the processed drift report data:\n\n{drift_report}\n\nProvide a plan to resolve this drift."

    initial_state = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
    }

    agent_output = ""
    for event in graph.stream(initial_state):
        for node, data in event.items():
            if not data:
                continue
            messages = data.get("messages") or []
            if messages:
                agent_output = messages[-1].content

    # Print out to the terminal as usual
    print(f"\n[Agent Response]:\n{agent_output}")

    # NEW: Automatically export the solution to a Markdown file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"drift_reconciliation_report_{timestamp}.md"
    report_path = os.path.join(TERRAFORM_DIR, report_filename)

    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# Terraform Drift Reconciliation Report\n")
            f.write(f"**Generated on:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("## Amazon Nova Pro Analysis & Action Plan\n\n")
            f.write(agent_output)
        print(f"\n[Success] Report successfully written to: {report_path}")
    except Exception as e:
        print(f"\n[Warning] Failed to write report file: {str(e)}")