import os
from datetime import datetime
from github import Github, Auth, GithubException, UnknownObjectException
from dotenv import load_dotenv
import subprocess
import json
import re
import shutil
import tempfile

load_dotenv()


REPO_ROOT = r"D:\aws-terraform-drift-reconciler"

UNPATCHABLE_BLOCK_FIELDS = {
    "aws_security_group": {"ingress", "egress"},
}

def is_unpatchable_finding(resource_id: str, changes: dict) -> bool:
    resource_type = resource_id.split(".")[0]
    unpatchable = UNPATCHABLE_BLOCK_FIELDS.get(resource_type)
    if not unpatchable:
        return False
    # True only if every changed field is one of the known-unpatchable block fields —
    # if the SG has some other real attribute change mixed in, still create the PR.
    return bool(changes) and all(field in unpatchable for field in changes)

def to_repo_relative_path(local_path: str) -> str:
    """Convert an absolute local path to a repo-relative, forward-slash path
    that GitHub's API expects."""
    rel = os.path.relpath(local_path, REPO_ROOT)
    return rel.replace("\\", "/")


def is_hcledit_available() -> bool:
    return shutil.which("hcledit") is not None


def close_superseded_prs(repo, resource_id: str, base_branch: str):
    """Close older OPEN drift PRs for the same resource before opening a new one.
    Only touches currently-open PRs — a resource that drifted, was fixed (PR merged
    or closed), and later drifts again independently will NOT be suppressed, since
    get_pulls(state='open') no longer sees the earlier resolved PR at all."""
    safe_id = resource_id.replace(".", "-")
    open_prs = repo.get_pulls(state="open", base=base_branch)
    for pr in open_prs:
        if pr.head.ref.startswith(f"drift-fix/{safe_id}-"):
            pr.create_issue_comment("Superseded by a newer run for the same drifted resource; closing.")
            pr.edit(state="closed")


def create_drift_pr(
        resource_id: str,
        pr_title: str,
        drift_summary: str,
        plan_output: str,
        file_path: str,
        file_content: str,
        risk_level: str = "LOW",
        base_branch: str = None):
    token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO")
    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo = g.get_repo(repo_name)

    base_branch = base_branch or os.getenv("GITHUB_BASE_BRANCH", "main")

    # Prevent duplicate open PRs for the same resource across repeated runs.
    close_superseded_prs(repo, resource_id, base_branch)

    safe_id = resource_id.replace(".", "-")
    head_branch = f"drift-fix/{safe_id}-{int(datetime.utcnow().timestamp())}"

    base_ref = repo.get_git_ref(f"heads/{base_branch}")
    repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_ref.object.sha)

    try:
        existing = repo.get_contents(file_path, ref=head_branch)
        print(f"[DEBUG] existing file found, sha={existing.sha}")
        repo.update_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            sha=existing.sha,
            branch=head_branch,
        )
        print(f"[DEBUG] update_file succeeded for {file_path}")
    except UnknownObjectException:
        print(f"[DEBUG] get_contents 404'd, creating new file at {file_path}")
        repo.create_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            branch=head_branch,
        )
    except GithubException as e:
        print(f"[ERROR] Unexpected GitHub API failure: status={e.status} data={e.data}")
        raise

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
        pass

    print(f"🎉 PR Created: {pr.html_url}")
    return pr


def create_drift_pr_for_mode(finding: dict, mode: str):
    resource_id = finding["resource_id"]
    risk_level = finding["risk_level"]
    is_deleted = finding.get("status") == "deleted_externally"

    if mode == "code_to_reality" and is_unpatchable_finding(resource_id, finding.get("changes", {})):
        print(f"[SKIP] {resource_id}: drift is on a computed block field "
              f"(ingress/egress) with no HCL block to patch — skipping PR, "
              f"relying on lifecycle.ignore_changes + PagerDuty alert instead.")
        return None

    if mode == "code_to_reality" and finding.get("file_path"):
        file_path = finding["file_path"]
        patched_file_content = apply_changes_to_file(
            file_path, resource_id, finding["changes"], deleted=is_deleted
        )
        pr_title = f"Drift fix: {resource_id} [{risk_level}]"
        content = patched_file_content
        target_path = to_repo_relative_path(file_path)
    else:
        pr_title = f"Drift fix: {resource_id} [{risk_level}] (report only)"
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
    """Read-only lookup — safe to read the real local file directly, never writes."""
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


def apply_changes_to_file(file_path, resource_id, changes, deleted=False):
    """Patch a TEMP COPY of the file via hcledit — the real local file on disk
    is never modified. Returns the patched content as a string for upload to GitHub."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tf")
    os.close(tmp_fd)
    shutil.copy(file_path, tmp_path)

    try:
        if not is_hcledit_available():
            print(f"[WARN] hcledit not found on PATH — skipping auto-patch for {resource_id}. "
                  f"Install from https://github.com/minamijoyo/hcledit/releases to enable this.")
            with open(tmp_path, encoding="utf-8") as f:
                return f.read()

        if deleted:
            result = subprocess.run(
                ["hcledit", "block", "rm", f"resource.{resource_id}", "-f", tmp_path, "-u"],
                check=False, capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"[WARN] hcledit block rm failed for {resource_id}: {result.stderr}")
        else:
            for field, vals in changes.items():
                if "." in field or "[" in field:
                    continue
                try:
                    subprocess.run(
                        ["hcledit", "attribute", "set", f"resource.{resource_id}.{field}",
                         json.dumps(vals["after"]), "-f", tmp_path, "-u"],
                        check=False,
                    )
                except FileNotFoundError:
                    print(f"[WARN] hcledit invocation failed for {resource_id}.{field} — skipping.")

        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.remove(tmp_path)