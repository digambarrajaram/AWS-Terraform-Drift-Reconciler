import os
from datetime import datetime
from github import Github, Auth, GithubException, UnknownObjectException
from dotenv import load_dotenv
import subprocess
import json
import re
import subprocess

load_dotenv()

def create_drift_pr(
        resource_id: str,
        pr_title: str,
        drift_summary: str,
        plan_output: str,
        file_path: str,
        file_content: str,
        risk_level: str = "LOW",   # LOW | MEDIUM | HIGH
        base_branch: str = None):
    token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO")
    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo = g.get_repo(repo_name)

    base_branch = base_branch or os.getenv("GITHUB_BASE_BRANCH", "main")
    head_branch = f"drift-fix/{resource_id}-{int(datetime.utcnow().timestamp())}"

    # 1. Create a fresh branch off base
    base_ref = repo.get_git_ref(f"heads/{base_branch}")
    repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_ref.object.sha)

    # 2. Commit the fix onto the new branch (create or update)
    try:
        existing = repo.get_contents(file_path, ref=head_branch)
        repo.update_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            sha=existing.sha,
            branch=head_branch,
        )
    except UnknownObjectException:
        repo.create_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            branch=head_branch,
        )

    pr_body = f"""## Drift detected: `{resource_id}`

**Risk level:** {risk_level}

### Summary
{drift_summary}

### Terraform Plan
```text
{plan_output}
```

_Opened automatically by AWS Terraform Drift Reconciler. Do not merge without review._
"""

    pr = repo.create_pull(
        title=pr_title,
        body=pr_body,
        head=head_branch,
        base=base_branch,
        draft=(risk_level == "HIGH"),
    )

    try:
        pr.add_to_labels("drift-reconciler", f"risk:{risk_level.lower()}")
    except GithubException:
        pass  # labels must pre-exist in repo; don't fail the PR over this

    print(f"🎉 PR Created: {pr.html_url}")
    return pr


def create_drift_pr_for_mode(finding: dict, mode: str):
    resource_id = finding["resource_id"]
    risk_level = finding["risk_level"]

    if mode == "code_to_reality" and finding.get("file_path"):
        file_path = finding["file_path"]
        original = get_resource_block_text(file_path, *resource_id.split(".", 1))
        patched_file_content = apply_changes_to_file(file_path, resource_id, finding["changes"])
        pr_title = f"Drift fix (accept live state): {resource_id} [{risk_level}]"
        content = patched_file_content
        target_path = file_path
    else:
        pr_title = f"Drift fix (revert to code): {resource_id} [{risk_level}]"
        content = (f"# Drift report: {resource_id}\n\n{finding['drift_summary']}\n\n"
                   f"```\n{finding['plan_output']}\n```\n\n"
                   f"Merging is a no-op on code — run `terraform apply` to revert AWS.")
        target_path = f"drift-reports/{resource_id.replace('.', '-')}.md"

    return create_drift_pr(
        resource_id=resource_id,
        pr_title=pr_title,
        drift_summary=finding["drift_summary"],
        plan_output=finding["plan_output"],
        file_path=target_path,
        file_content=content,
        risk_level=risk_level,
    )

def get_resource_block_text(file_path, resource_type, resource_name):
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(resource_name)}"\s*\{{')
    m = pattern.search(content)
    if not m:
        return None
    brace_pos = content.index("{", m.end() - 1)
    depth, i = 1, brace_pos + 1
    while depth > 0 and i < len(content):
        depth += 1 if content[i] == "{" else -1 if content[i] == "}" else 0
        i += 1
    return content[m.start():i]


def apply_changes_to_file(file_path, resource_id, changes):
    """Patch scalar attribute values via hcledit; skip fields it can't handle cleanly."""
    for field, vals in changes.items():
        if "." in field or "[" in field:   # nested/list fields — too risky for auto-patch
            continue
        subprocess.run(
            ["hcledit", "attribute", "set", f"resource.{resource_id}.{field}",
             json.dumps(vals["after"]), "-f", file_path, "-u"],
            check=False,
        )
    with open(file_path, encoding="utf-8") as f:
        return f.read()


#if __name__ == "__main__":
#    create_drift_pr(
#       resource_id="sg-0abc123",
#      pr_title="Drift fix: sg-0abc123 [MEDIUM]",
#        drift_summary="Ingress rule 22/tcp was manually opened to 0.0.0.0/0, not present in Terraform state.",
#        plan_output="~ resource \"aws_security_group\" \"web\" { ... }",
#        file_path="infra/security_groups.tf",
#        file_content="# patched HCL content here",
#        risk_level="MEDIUM",
#    )