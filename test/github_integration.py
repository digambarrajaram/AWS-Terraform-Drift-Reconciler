import os
from datetime import datetime
from github import Github, Auth, GithubException, UnknownObjectException
from dotenv import load_dotenv
import subprocess
import json
import re
import shutil
import tempfile

import drift_history

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


def _safe_label(account_label: str) -> str:
    """Sanitise *account_label* for use in git branch names and PR titles."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", account_label)


def is_hcledit_available() -> bool:
    return shutil.which("hcledit") is not None


def close_superseded_prs(repo, resource_id: str, account_label: str, base_branch: str, is_rollback: bool = False):
    """Close older OPEN drift PRs for the same resource+account before opening
    a new one.  Only touches currently-open PRs — a resource that drifted, was
    fixed (PR merged or closed), and later drifts again independently will NOT
    be suppressed, since get_pulls(state='open') no longer sees the earlier
    resolved PR at all.

    When *is_rollback* is True, only closes other rollback PRs.  When False,
    only closes regular drift-fix PRs.  The two types never supersede each
    other — a human may want both a fix and a rollback open simultaneously."""
    safe_id = resource_id.replace(".", "-")
    safe_account = _safe_label(account_label)
    prefix = f"drift-fix/{safe_account}/{safe_id}-"
    open_prs = repo.get_pulls(state="open", base=base_branch)
    for pr in open_prs:
        if not pr.head.ref.startswith(prefix):
            continue
        branch_is_rollback = "-rollback-" in pr.head.ref
        if branch_is_rollback != is_rollback:
            continue  # never cross-type supersede
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
        base_branch: str = None,
        account_label: str = "default",
        changes: dict | None = None,
        is_rollback: bool = False):
    token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO")
    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo = g.get_repo(repo_name)

    base_branch = base_branch or os.getenv("GITHUB_BASE_BRANCH", "main")

    safe_account = _safe_label(account_label)

    # Prevent duplicate open PRs for the same resource+account across
    # repeated runs.
    close_superseded_prs(repo, resource_id, account_label, base_branch, is_rollback=is_rollback)

    safe_id = resource_id.replace(".", "-")
    head_branch = f"drift-fix/{safe_account}/{safe_id}-{int(datetime.utcnow().timestamp())}"

    pr_title = f"[{account_label}] {pr_title}"

    base_ref = repo.get_git_ref(f"heads/{base_branch}")
    repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_ref.object.sha)

    try:
        existing = repo.get_contents(file_path, ref=head_branch)
        #print(f"[DEBUG] existing file found, sha={existing.sha}")
        repo.update_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            sha=existing.sha,
            branch=head_branch,
        )
        #print(f"[DEBUG] update_file succeeded for {file_path}")
    except UnknownObjectException:
        #print(f"[DEBUG] get_contents 404'd, creating new file at {file_path}")
        repo.create_file(
            path=file_path,
            message=pr_title,
            content=file_content,
            branch=head_branch,
        )
    except GithubException as e:
        #print(f"[ERROR] Unexpected GitHub API failure: status={e.status} data={e.data}")
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

    # Store a drift baseline so future rollbacks can reverse this exact
    # fix by swapping before/after and re-patching.  One baseline per
    # resource per PR — never overwritten.
    if changes:
        safe_rid = resource_id.replace(".", "-")
        baseline_path = f".drift-baselines/pr-{pr.number}/{safe_rid}.json"
        baseline_content = json.dumps(
            {
                "resource_id": resource_id,
                "file_path": file_path,
                "changes": changes,
                "captured_at": datetime.utcnow().isoformat() + "Z",
            },
            indent=2,
        )
        try:
            repo.create_file(
                path=baseline_path,
                message=f"[baseline] Store drift baseline for {resource_id}",
                content=baseline_content,
                branch=head_branch,
            )
        except GithubException as e:
            print(f"  ⚠ Failed to store drift baseline for {resource_id}: {e}")

    # Append to the per-account drift history for trend reporting.
    try:
        drift_history.append_entry(
            resource_id=resource_id,
            account_label=account_label,
            region=os.environ.get("AWS_REGION", "unknown"),
            pr_number=pr.number,
            pr_type="rollback" if is_rollback else "fix",
            severity=risk_level,
            fields_changed=list(changes.keys()) if changes else [],
            drift_summary=drift_summary,
        )
    except Exception as exc:
        print(f"  ⚠ Failed to append drift history: {exc}")

    print(f"🎉 PR Created: {pr.html_url}")
    return pr


def create_drift_pr_for_mode(finding: dict, mode: str, account_label: str = "default"):
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
    elif finding.get("status") in ("unmanaged", "unmanaged_tagged"):
        pr_title = f"Unmanaged resource: {resource_id} [{risk_level}]"
        content = (
            f"# Unmanaged resource: {resource_id}\n\n"
            f"{finding['drift_summary']}\n\n"
            f"```json\n{finding['plan_output']}\n```\n\n"
            f"**Action:** Import this resource into Terraform or create "
            f"the corresponding `.tf` resource block, then re-run the "
            f"drift reconciler to track it."
        )
        target_path = f"drift-reports/{resource_id.replace('.', '-')}.md"
    else:
        pr_title = f"Drift fix: {resource_id} [{risk_level}] (report only)"
        content = (f"# Drift report: {resource_id}\n\n{finding['drift_summary']}\n\n"
                   f"```\n{finding['plan_output']}\n```\n\n"
                   f"Merging is a no-op on code — run `terraform apply` to revert AWS.")
        target_path = f"drift-reports/{resource_id.replace('.', '-')}.md"

    # Append cost estimate when available.
    cost = finding.get("cost_impact")
    if cost:
        runtime = cost.get("runtime_hours")
        runtime_line = (
            f"- Running for: {runtime:.1f} hours" if runtime is not None
            else "- Running for: unknown (≥4 hours)"
        )
        content += (
            f"\n\n### Cost Estimate\n\n"
            f"- Hourly rate: ${cost['hourly_usd']:.4f}\n"
            f"- Estimated monthly: **${cost['monthly_estimate_usd']:.2f}**\n"
            f"- Accrued since creation: ${cost['accrued_usd']:.2f}\n"
            f"{runtime_line}\n"
        )

    return create_drift_pr(
        resource_id=resource_id,
        pr_title=pr_title,
        drift_summary=finding["drift_summary"],
        plan_output=finding["plan_output"],
        file_path=target_path,
        file_content=content,
        risk_level=risk_level,
        account_label=account_label,
        changes=finding.get("changes") if finding.get("file_path") else None,
    )


def _apply_changes_batch(file_path: str, findings: list[dict]) -> str:
    """Apply changes from multiple findings to one temp copy of the file.

    Each finding's before→after replacements (or block removal for
    deleted_externally) are applied sequentially so every drift fix
    lands in a single commit."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tf")
    os.close(tmp_fd)
    shutil.copy(file_path, tmp_path)

    try:
        for f in findings:
            resource_id = f["resource_id"]
            changes = f.get("changes", {})
            deleted = f.get("status") == "deleted_externally"

            if not is_hcledit_available():
                patched = _regex_patch_tf_file(tmp_path, resource_id, changes, deleted)
                if patched is not None:
                    with open(tmp_path, "w", encoding="utf-8") as fh:
                        fh.write(patched)
            else:
                if deleted:
                    subprocess.run(
                        ["hcledit", "block", "rm", f"resource.{resource_id}", "-f", tmp_path, "-u"],
                        check=False, capture_output=True, text=True,
                    )
                else:
                    for field, vals in changes.items():
                        if "." in field or "[" in field:
                            continue
                        if _is_complex_value(vals.get("after")):
                            print(f"  ⚠ {resource_id}.{field}: complex value (map/list) — "
                                  f"skipping auto-patch, requires manual HCL edit")
                            continue
                        subprocess.run(
                            ["hcledit", "attribute", "set", f"resource.{resource_id}.{field}",
                             json.dumps(str(vals["after"])), "-f", tmp_path, "-u"],
                            check=False,
                        )

        with open(tmp_path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.remove(tmp_path)


def create_drift_pr_for_file(findings: list[dict], mode: str, account_label: str = "default"):
    """Create a single PR that reconciles every drift finding for one file.

    All before→after value replacements and deleted-resource block removals
    are applied to the same temp copy so related changes ship together."""
    if not findings:
        return None

    file_path = findings[0].get("file_path")
    if not file_path:
        return None

    # Skip findings that are unpatchable (computed block fields only).
    actionable = [f for f in findings
                  if not is_unpatchable_finding(f["resource_id"], f.get("changes", {}))]
    if not actionable:
        return None

    patched_content = _apply_changes_batch(file_path, actionable)

    resource_ids = [f["resource_id"] for f in actionable]
    highest_risk = "LOW"
    for level in ("HIGH", "MEDIUM", "LOW"):
        if any(f.get("risk_level") == level for f in actionable):
            highest_risk = level
            break

    count = len(actionable)
    pr_title = f"Drift fix: {count} resource(s) [{highest_risk}]"

    drift_summary = "\n".join(
        f"- **`{f['resource_id']}`**: {f['drift_summary']}" for f in actionable
    )
    plan_output = "\n\n".join(
        f"### `{f['resource_id']}`\n```text\n{f['plan_output']}\n```" for f in actionable
    )

    # Use the first resource id as the git-ref anchor with a batch-size
    # suffix so the branch name stays valid (no commas / spaces / special
    # chars beyond what git allows).  The full resource list is in the
    # PR title and body.
    branch_id = resource_ids[0] if count == 1 else f"{resource_ids[0]}-batch-{count}"

    pr = create_drift_pr(
        resource_id=branch_id,
        pr_title=pr_title,
        drift_summary=drift_summary,
        plan_output=plan_output,
        file_path=to_repo_relative_path(file_path),
        file_content=patched_content,
        risk_level=highest_risk,
        account_label=account_label,
    )
    if pr is None:
        return None

    # Store a baseline per resource under the same PR directory so each
    # one can be rolled back independently.
    token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO")
    g = Github(auth=Auth.Token(token))
    repo = g.get_repo(repo_name)

    for f in actionable:
        changes = f.get("changes")
        if not changes:
            continue
        rid = f["resource_id"]
        safe_rid = rid.replace(".", "-")
        baseline_path = f".drift-baselines/pr-{pr.number}/{safe_rid}.json"
        baseline_content = json.dumps(
            {
                "resource_id": rid,
                "file_path": to_repo_relative_path(file_path),
                "changes": changes,
                "captured_at": datetime.utcnow().isoformat() + "Z",
            },
            indent=2,
        )
        try:
            repo.create_file(
                path=baseline_path,
                message=f"[baseline] Store drift baseline for {rid}",
                content=baseline_content,
                branch=pr.head.ref,
            )
        except GithubException as e:
            print(f"  ⚠ Failed to store drift baseline for {rid}: {e}")

    # One history entry per resource in the batch.
    for f in actionable:
        try:
            drift_history.append_entry(
                resource_id=f["resource_id"],
                account_label=account_label,
                region=os.environ.get("AWS_REGION", "unknown"),
                pr_number=pr.number,
                pr_type="batch",
                severity=f.get("risk_level", "LOW"),
                fields_changed=list(f.get("changes", {}).keys()),
                drift_summary=f.get("drift_summary", ""),
            )
        except Exception as exc:
            print(f"  ⚠ Failed to append drift history for {f['resource_id']}: {exc}")

    return pr


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


def _extract_field_values(
    plan_json: dict,
    resource_address: str,
    fields: list[str],
) -> tuple[str, dict[str, str]]:
    """Extract live field values for *resource_address* from a terraform
    plan JSON (``terraform show -json`` output).

    Returns ``(outcome, values)`` where *outcome* is one of:

    ``"present"``
        Resource found, *values* contains ``change.before`` for each
        requested field.
    ``"no_diff"``
        Resource found but ``change.before == change.after`` for every
        requested field — nothing to reconcile.
    ``"not_found"``
        Resource address does not appear in ``resource_changes[]`` or
        the resource is absent from the plan entirely (e.g. deleted
        externally).

    Always reads from ``change.before`` (live AWS state), NOT
    ``change.after`` (what the code proposes).  Getting this backwards
    makes the freshness check silently useless.
    """
    resource_changes = plan_json.get("resource_changes", [])
    for rc in resource_changes:
        if rc.get("address") != resource_address:
            continue
        change = rc.get("change", {})
        before = change.get("before", {})
        after = change.get("after", {})

        if not before:
            # Resource has no live state — possibly deleted externally.
            return ("not_found", {})

        all_same = True
        values: dict[str, str] = {}
        for field in fields:
            b_val = before.get(field)
            a_val = after.get(field)
            if b_val is not None:
                values[field] = str(b_val)
            if b_val != a_val:
                all_same = False

        if all_same and values:
            return ("no_diff", values)
        return ("present", values)

    return ("not_found", {})


def _is_complex_value(val) -> bool:
    """Return True when *val* looks like a JSON-serialised map or list.

    The drift report serialises HCL maps / lists as JSON strings (e.g.
    ``{"Name":"WebServer"}``).  Neither the hcledit nor the regex fallback
    patcher can convert these back to valid HCL reliably, so they must be
    skipped with a warning rather than blindly inserted as string literals
    (which produces ``tags = "{'Name': 'WebServer'}"`` — invalid HCL)."""
    if isinstance(val, dict):
        return True
    if isinstance(val, list):
        return True
    if isinstance(val, str):
        stripped = val.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return True
    return False


def _regex_patch_tf_file(file_path: str, resource_id: str, changes: dict, deleted: bool) -> str | None:
    """Regex-based fallback when hcledit is not available.

    For *deleted* resources the entire resource block is removed.  For
    drifted attributes each before → after value is replaced inside the
    block.  Returns the patched content on success, or None when the
    resource block cannot be located."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    if "." not in resource_id:
        return None
    want_type, want_name = resource_id.split(".", 1)

    lines = content.splitlines()
    in_block = False
    depth = 0
    block_start = 0
    block_end = len(lines) - 1

    for i, line in enumerate(lines):
        m = re.search(r'resource\s+"([^"]+)"\s+"([^"]+)"', line)
        if m and m.group(1) == want_type and m.group(2) == want_name:
            in_block = True
            block_start = i
            depth = line.count("{") - line.count("}")
            continue
        if in_block:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                block_end = i
                break

    if not in_block:
        return None

    if deleted:
        return "\n".join(lines[:block_start] + lines[block_end + 1:])

    applied = False
    for i in range(block_start, block_end + 1):
        for field, vals in changes.items():
            if _is_complex_value(vals.get("before")) or _is_complex_value(vals.get("after")):
                continue  # map/list — can't safely regex-replace, skip
            before_val = str(vals.get("before", ""))
            after_val = str(vals.get("after", ""))
            if before_val and before_val in lines[i]:
                lines[i] = lines[i].replace(before_val, after_val, 1)
                applied = True
                break

    return "\n".join(lines) if applied else None


def apply_changes_to_file(file_path, resource_id, changes, deleted=False):
    """Patch a TEMP COPY of the file via hcledit — the real local file on disk
    is never modified. Returns the patched content as a string for upload to GitHub."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tf")
    os.close(tmp_fd)
    shutil.copy(file_path, tmp_path)

    try:
        if not is_hcledit_available():
            print(f"[WARN] hcledit not found on PATH — applying regex fallback for {resource_id}. "
                  f"Install from https://github.com/minamijoyo/hcledit/releases for more reliable patching.")
            patched = _regex_patch_tf_file(tmp_path, resource_id, changes, deleted)
            if patched is not None:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(patched)
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
                if _is_complex_value(vals.get("after")):
                    print(f"  ⚠ {resource_id}.{field}: complex value (map/list) — "
                          f"skipping auto-patch, requires manual HCL edit")
                    continue
                try:
                    subprocess.run(
                        ["hcledit", "attribute", "set", f"resource.{resource_id}.{field}",
                         json.dumps(str(vals["after"])), "-f", tmp_path, "-u"],
                        check=False,
                    )
                except FileNotFoundError:
                    print(f"[WARN] hcledit invocation failed for {resource_id}.{field} — skipping.")

        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.remove(tmp_path)