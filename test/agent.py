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
from langchain_core.messages import AIMessage

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
            #shell=True  # Added shell=True to safely locate the terraform binary
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
            #shell=True
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
            #shell=True
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


def build_drift_summary(resource: dict) -> str:
    if resource.get("status") == "deleted_externally":
        return "Resource was deleted outside of Terraform (found in state, missing from AWS)."
    changes = resource.get("changes", {})
    lines = [f"- `{field}`: before `{v.get('before')}` → after `{v.get('after')}`" for field, v in changes.items()]
    return "\n".join(lines)


def build_drift_findings(drift_report_json: dict) -> list[dict]:
    findings = []
    if drift_report_json.get("report_type") != "drift":
        return findings

    for resource in drift_report_json.get("resources", []):
        # deleted_externally resources have no "changes" but are still real findings
        if not resource.get("changes") and resource.get("status") != "deleted_externally":
            continue
        findings.append({
            "resource_id": resource["address"],
            "risk_level": map_risk(resource.get("security_impact")),
            "drift_summary": build_drift_summary(resource),
            "plan_output": json.dumps(resource.get("changes") or {"status": resource.get("status")}, indent=2),
            "file_path": resource.get("file_path"),
            "changes": resource.get("changes", {}),
            "status": resource.get("status"),
        })
    return findings



def agent_node(state: State):
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
        return {
            "messages": [AIMessage(content="STATUS: NO_DRIFT\nNo configuration drift detected.")],
            "drift_detected": False,
            "drift_findings": [],
        }

    response = llm.invoke(state["messages"])
    findings = build_drift_findings(drift_report_json)
    return {
        "messages": [response],
        "drift_detected": True,
        "drift_findings": findings,
    }


def drift_alert(state: State):
    if not state["drift_detected"]:
        return {"messages": []}
    for finding in state["drift_findings"]:
        result = pga.trigger_pagerduty_alert(
            summary=f"Drift detected: {finding['resource_id']}",
            severity="error",
            source="terraform-drift-engine",
            dedup_key=f"drift-{finding['resource_id']}",
        )
        #print(f"[DEBUG] PagerDuty response: {result}")
    return {"messages": []}
def drift_pr_from_finding(state: State):
    if not state["drift_detected"]:
        return {"pr_urls": []}
    pr_urls = []
    for finding in state["drift_findings"]:
        pr = gi.create_drift_pr_for_mode(finding, "code_to_reality")
        if pr is not None:
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
        "      \"status\": null|\"deleted_externally\",\n"          # <-- new
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
        "   and never suggest re-adding, restoring, or recreating a deleted_externally resource.\n\n"
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