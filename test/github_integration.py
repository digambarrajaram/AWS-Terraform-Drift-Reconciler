import os
from datetime import datetime
from github import Github, Auth, GithubException, UnknownObjectException
from dotenv import load_dotenv

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