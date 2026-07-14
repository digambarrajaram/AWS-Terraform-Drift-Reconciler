import json
import shutil
import subprocess
import sys


def run_trivy_tf_scan(file_path: str):
    # 1. Verify Trivy is installed on the system
    if not shutil.which("trivy"):
        print("Error: Trivy CLI is not installed or not in PATH.")
        sys.exit(1)

    # 2. Build the Trivy command targeting the Terraform file
    # Uses JSON format for easy parsing in Python
    command = ["trivy", "config", "--format", "json", file_path]

    try:
        # 3. Execute the scan
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, shell=True
        )

        # 4. Parse and display JSON output
        scan_data = json.loads(result.stdout)
        print("--- Scan Completed Successfully ---")
        print(json.dumps(scan_data, indent=2))

    except subprocess.CalledProcessError as e:
        # Trivy returns exit code 1 if vulnerabilities are found
        if e.stdout:
            try:
                scan_data = json.loads(e.stdout)
                print("--- Scan Completed (Vulnerabilities Found) ---")
                print(json.dumps(scan_data, indent=2))
            except json.JSONDecodeError:
                print(f"Scan failed. Stderr: {e.stderr}")
        else:
            print(f"An error occurred: {e.stderr}")


if __name__ == "__main__":
    # Replace with the path to your Terraform file or directory
    TARGET_TF_FILE = r"D:\aws-terraform-drift-reconciler\test\ec2_terraform\main.tf"
    run_trivy_tf_scan(TARGET_TF_FILE)