"""
LangGraph-based agent that scans Terraform code with Trivy
and loops until all misconfigurations are fixed.

Usage:
    python test/trivy_agent.py [--tf-dir <path>] [--max-iterations <N>]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Annotated, Literal

from langchain_aws import ChatBedrockConverse
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

# ==========================================
# STATE DEFINITION
# ==========================================


class FixEntry(TypedDict):
    """One fix applied to a file."""

    rule_id: str
    file_path: str
    description: str


class State(TypedDict):
    tf_dir: str
    scan_results: list[dict]
    issues: list[dict]
    fixes_applied: Annotated[list[FixEntry], lambda a, b: a + b]
    iteration: int
    max_iterations: int
    passed: bool
    trivy_error: bool
    messages: Annotated[list[str], lambda a, b: a + b]


# ==========================================
# HELPERS
# ==========================================


TF_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')

# Trivy's "Status" field on a misconfiguration check. Only "FAIL" means the
# check failed. "CRITICAL"/"HIGH"/etc are SEVERITY values, not statuses.
_FAILING_STATUSES = {"FAIL", "FAILED"}

# How many times to ask the LLM to repair a syntax error before giving up.
MAX_SYNTAX_REPAIR_ATTEMPTS = 3

# Order in which issues get fixed within an iteration, highest first.
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}

_IGNORE_COMMENT_RE = re.compile(r'^\s*#\s*trivy:ignore:([A-Za-z0-9\-]+)\s*(?:--\s*(.*))?$', re.MULTILINE)

_llm = None

# Cache for provider schema loaded via `terraform providers schema -json`,
# keyed by tf_dir.  Shared across fix attempts within the same run.
_schema_cache: dict[str, dict | None] = {}          # tf_dir → parsed schema or None (unavailable)

# Attribute names that exist in every Terraform resource block and are
# NOT provider-specific, so they should never be flagged as invalid.
_HCL_META_ARGS = frozenset({
    "depends_on", "lifecycle", "provider", "count",
    "for_each", "connection", "provisioner", "source",
    "tags", "tags_all",
})


def _get_provider_schema(tf_dir: str) -> dict | None:
    """Fetch and cache the real AWS provider schema via terraform CLI.
    This is ground truth for valid attribute names — not an LLM guess."""
    if tf_dir in _schema_cache:
        return _schema_cache[tf_dir]
    try:
        result = subprocess.run(
            ["terraform", "providers", "schema", "-json"],
            cwd=tf_dir, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0 or not result.stdout:
            print(f"  ⚠ Could not fetch provider schema: {result.stderr[:200]}")
            _schema_cache[tf_dir] = None
            return None
        _schema_cache[tf_dir] = json.loads(result.stdout)
        return _schema_cache[tf_dir]
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as e:
        print(f"  ⚠ Provider schema fetch failed: {e}")
        _schema_cache[tf_dir] = None
        return None


def _valid_attributes_for(tf_dir: str, resource_type: str) -> set[str] | None:
    """Return the set of real top-level attribute names for a resource type,
    or None if the schema couldn't be determined (caller should then skip
    this check rather than false-reject)."""
    schema = _get_provider_schema(tf_dir)
    if not schema:
        return None
    for provider_data in schema.get("provider_schemas", {}).values():
        rs = provider_data.get("resource_schemas", {}).get(resource_type)
        if rs:
            return set(rs.get("block", {}).get("attributes", {}).keys())
    return None


def _find_invalid_attributes(block_text: str, valid_attrs: set[str]) -> list[str]:
    """Find top-level attribute assignments in a resource block that aren't
    in the real provider schema.

    Dynamically detects the minimum attribute indentation instead of
    assuming 2 spaces, skips HCL meta-arguments (depends_on etc.), and
    skips blocks annotated with ``# trivy:ignore`` comments.
    """
    lines = block_text.splitlines()
    if len(lines) < 2:
        return []

    # Find the minimum indentation among attribute-like lines so we only
    # flag top-level attributes, not keys nested inside sub-blocks.
    indents: list[int] = []
    for line in lines[1:]:  # skip the resource declaration line
        m = re.match(r"^(\s+)([a-z_][a-z0-9_]*)\s*=", line)
        if m:
            indents.append(len(m.group(1)))
    if not indents:
        return []
    base_indent = min(indents)

    # Check if the block has a trivy:ignore comment — if so, the LLM was
    # told to leave it unchanged, so don't flag anything.
    for line in lines:
        if _IGNORE_COMMENT_RE.search(line):
            return []

    attr_re = re.compile(rf"^\s{{{base_indent}}}([a-z_][a-z0-9_]*)\s*=")
    invalid = []
    for line in lines[1:]:
        m = attr_re.match(line)
        if m:
            attr = m.group(1)
            if attr not in _HCL_META_ARGS and attr not in valid_attrs:
                invalid.append(attr)
    return invalid


def _get_llm():
    """Lazily construct the Bedrock LLM client (same model as the main
    drift-reconciler pipeline), so it's only initialized if actually needed."""
    global _llm
    if _llm is None:
        _llm = ChatBedrockConverse(
            model="amazon.nova-pro-v1:0",
            temperature=0.1,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _llm


def _extract_hcl_block(text: str) -> str | None:
    """Pull the contents of a ```hcl / ```terraform fenced block from an
    LLM response, falling back to the raw text if no fence is present."""
    m = re.search(r"```(?:hcl|terraform)?\s*(.*?)\s*```", text, re.DOTALL)
    fixed = m.group(1).strip() if m else text.strip()
    return fixed or None


def _read_file(file_path: str) -> str | None:
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ⚠ Could not read {file_path}: {e}")
        return None


def _write_file(file_path: str, content: str) -> bool:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError as e:
        print(f"  ⚠ Could not write {file_path}: {e}")
        return False


def _parse_resource_blocks(content: str) -> list[tuple[str, str, int, int]]:
    """Return [(type, name, start_line, end_line)] for every resource block (1-idx).

    ponytail: uses naive ``str.count`` for brace tracking.  Braces inside
    quoted strings, heredocs, or ``jsonencode`` calls can throw off the
    depth counter.  A proper HCL tokeniser would be the upgrade path.
    Multi-line ``/* */`` comments that wrap resource blocks are not
    filtered — they are rare in practice.
    """
    blocks: list[tuple[str, str, int, int]] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = TF_RESOURCE_RE.search(lines[i])
        if m:
            # Single-line block where every opening brace is balanced by a
            # closing brace on the same line (e.g. ``resource "x" "y" {}``).
            # The inner loop's ``j > i`` guard would never fire for these,
            # so handle them here before scanning forward.
            stripped = lines[i].lstrip()
            if not stripped.startswith(("#", "//", "/*")) and lines[i].count("{") > 0 and lines[i].count("{") == lines[i].count("}"):
                blocks.append((m.group(1), m.group(2), i + 1, i + 1))
                i += 1
                continue

            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0 and j > i:
                    blocks.append((m.group(1), m.group(2), i + 1, j + 1))
                    i = j
                    break
        i += 1
    return blocks


def _run_trivy(tf_dir: str) -> dict:
    """Run `trivy config --format json` and return parsed output."""
    cmd = ["trivy", "config", "--format", "json", tf_dir]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.stdout:
            parsed = json.loads(result.stdout)
            return parsed
        if result.stderr:
            return {"error": result.stderr.strip(), "returncode": result.returncode}
        return {"error": "No output from trivy", "returncode": result.returncode}
    except (json.JSONDecodeError, OSError) as e:
        return {"error": str(e)}


def _extract_issues(trivy_output: dict, tf_dir: str) -> list[dict]:
    """Pull failed checks from Trivy's Results, using CauseMetadata for exact
    file/line/resource location instead of guessing from resolution text.

    Findings suppressed via ``# trivy:ignore:<rule-id>`` comments
    (placed on the line immediately above the resource block) are
    silently dropped — same convention as Trivy, Checkov, and tfsec.
    """
    results = trivy_output.get("Results", [])
    issues = []
    seen = set()
    for result in results:
        target = result.get("Target", "")
        for misconfig in result.get("Misconfigurations", []):
            status = (misconfig.get("Status") or "").upper()
            if status not in _FAILING_STATUSES:
                continue

            rule_id = misconfig.get("AVDID") or misconfig.get("ID") or "unknown"
            cause = misconfig.get("CauseMetadata") or {}
            resource_addr = cause.get("Resource", "")
            start_line = cause.get("StartLine")
            end_line = cause.get("EndLine")

            # ---- inline suppression check ----
            if start_line and rule_id and rule_id != "unknown":
                fpath = _resolve_file_path(tf_dir, target)
                if fpath:
                    suppressed = _is_suppressed(fpath, start_line, rule_id)
                    if suppressed:
                        continue  # human accepted this risk — don't report it

            # Dedup by (rule, file, resource) so two resources in the same
            # file that fail the same rule are NOT collapsed into one.
            key = (rule_id, target, resource_addr)
            if key in seen:
                continue
            seen.add(key)

            resource_type = None
            if resource_addr and "." in resource_addr and not resource_addr.startswith("module."):
                resource_type = resource_addr.split(".")[0]

            issues.append(
                {
                    "rule_id": rule_id,
                    "severity": misconfig.get("Severity", "UNKNOWN"),
                    "title": misconfig.get("Title", ""),
                    "description": misconfig.get("Description", ""),
                    "resolution": misconfig.get("Resolution", ""),
                    "target": target,
                    "resource": resource_addr,
                    "resource_type": resource_type,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
    return issues


def _is_suppressed(file_path: str, start_line: int, rule_id: str) -> bool:
    """Return True if *file_path* has ``# trivy:ignore:<rule_id>`` on one of
    the 3 lines immediately above the *resource block* that contains
    *start_line*.  Fail-open: returns False when the file can't be read.

    Trivy's StartLine often points at the *attribute* line inside a
    resource (e.g. ``cidr_ipv4`` on line 104), not the resource
    declaration (line 101).  We resolve the enclosing block first so
    the comment placed above the resource is always found regardless
    of where inside the block the finding lands.
    """
    content = _read_file(file_path)
    if content is None:
        return False
    lines = content.splitlines()

    # Walk the line upward to the enclosing resource-block start so the
    # suppression comment is found even for attribute-level findings.
    resource_start = start_line
    blocks = _parse_resource_blocks(content)
    for _t, _n, s, e in blocks:
        if s <= start_line <= e:
            resource_start = s
            break

    lo = max(0, resource_start - 4)
    hi = max(0, resource_start - 1)
    for i in range(lo, hi):
        if i >= len(lines):
            break
        m = _IGNORE_COMMENT_RE.search(lines[i])
        if m and m.group(1).strip() == rule_id:
            return True
    return False


def _resolve_file_path(tf_dir: str, target: str) -> str | None:
    """Resolve a Target/filename string to an actual file on disk."""
    candidates = [
        target,
        os.path.join(tf_dir, target),
        os.path.join(tf_dir, os.path.basename(target)),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

def _resolve_block(
    content: str,
    resource_addr: str | None,
    start_line: int | None,
    end_line: int | None,
    resource_type: str | None,
) -> tuple[int, int] | None:
    """Pick the (start, end) line range of the resource block to edit.

    Priority:
      1. Exact resource address match (type.name) — immune to line drift
         caused by an earlier fix in the same file during this iteration.
      2. The block whose line range CONTAINS Trivy's reported start_line.
         Works for both full-block findings and single-line/attribute-level
         findings (e.g. StartLine == EndLine pointing at one cidr_blocks
         line deep inside a much larger resource block) — unlike checking
         whether the reported range itself parses as a resource header.
      3. Last block of the matching resource_type.
      4. Last block in the file, as a final fallback.
    """
    lines = content.splitlines()
    blocks = _parse_resource_blocks(content)

    if resource_addr and "." in resource_addr and not resource_addr.startswith("module."):
        want_type, want_name = resource_addr.split(".", 1)
        for t, n, s, e in blocks:
            if t == want_type and n == want_name:
                return s, e

    if start_line and 1 <= start_line <= len(lines):
        containing = [b for b in blocks if b[2] <= start_line <= b[3]]
        if containing:
            # Innermost (tightest) containing block, in the rare case of overlap.
            _t, _n, s, e = min(containing, key=lambda b: b[3] - b[2])
            return s, e

    if resource_type:
        matching = [b for b in blocks if b[0] == resource_type]
        if matching:
            _t, _n, s, e = matching[-1]
            return s, e

    if blocks:
        _t, _n, s, e = blocks[-1]
        return s, e

    return None


def _try_cheap_regex_fix(content: str, block: tuple[int, int], resolution: str) -> tuple[str, str] | None:
    """Fast path for trivial 'Set X to <value>' resolutions.

    Handles boolean (true / false) and single-quoted string values.
    Anything else (block insertions, complex rewrites) returns None
    so _apply_fix falls through to the LLM.
    """
    m = re.search(r"Set\s+'([^']+)'\s+to\s+(true|false|'[^']*')", resolution, re.IGNORECASE)
    if not m:
        return None
    attr = m.group(1)
    value = m.group(2)  # "true", "false", or "'AES256'"
    start, end = block
    lines = content.splitlines()

    for i in range(start - 1, min(end, len(lines))):
        if re.match(rf"^\s+{re.escape(attr)}\s*=", lines[i]):
            lines[i] = re.sub(
                rf"(^\s+{re.escape(attr)}\s*=).*$", rf"\1 {value}", lines[i]
            )
            return "\n".join(lines), f"Set `{attr}` = {value}"

    # Attribute not found in this block — don't guess.  The LLM fallback
    # in _apply_fix can decide whether / where / how to add it.
    return None


def _llm_fix_block(block_text: str, issue: dict) -> str | None:
    """Ask the LLM to rewrite the exact flagged resource block."""
    system_prompt = (
        "You are a Terraform security remediation assistant. You are given "
        "exactly one Terraform resource block and a security finding about it. "
        "Make the minimum change needed to resolve the finding while keeping "
        "the resource type, name, and all unrelated attributes unchanged. "
        "Use ONLY real, currently valid Terraform AWS provider argument names "
        "for this resource type — never invent or guess an attribute name.\n\n"
        "Some findings require a genuine organization-specific decision you "
        "cannot make correctly — for example, the exact CIDR range a security "
        "group should restrict to, or a specific KMS key / IAM role ARN that "
        "must reference a real resource in the account. A guessed placeholder "
        "value for these will typically still fail the same scanner rule, "
        "since it cannot be verified as actually correct or actually secure.\n\n"
        "If — and only if — the finding falls into this category, do NOT "
        "guess a value. Instead return the block completely UNCHANGED, but "
        "add exactly one comment line directly above the resource block, in "
        f"this exact format:\n"
        f"  # trivy:ignore:{issue['rule_id']} -- <one short sentence: what a human must decide>\n"
        "Do not add this comment for findings you CAN resolve unambiguously "
        "(e.g. enabling encryption, versioning, logging, a boolean flag, or "
        "any fix that doesn't depend on account-specific values) — for those, "
        "make the real fix as normal.\n\n"
        "Return ONLY the corrected (or annotated) block inside a single "
        "```hcl code block, with no other commentary."
    )
    user_prompt = (
        f"Finding: {issue['rule_id']} — {issue['title']}\n"
        f"Severity: {issue['severity']}\n"
        f"Description: {issue['description']}\n"
        f"Resolution: {issue['resolution']}\n\n"
        f"Resource block:\n```hcl\n{block_text}\n```"
    )
    try:
        llm = _get_llm()
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
    except Exception as e:
        print(f"  ⚠ LLM fix call failed for {issue['rule_id']}: {e}")
        return None

    return _extract_hcl_block(getattr(response, "content", "") or "")

def _block_matches_resource(block_text: str, resource_addr: str | None) -> bool:
    """Guard against the LLM returning a mangled block — verify the
    rewritten text still declares the same resource type + name."""
    if not resource_addr or "." not in resource_addr:
        return True  # nothing to check against, don't block on this
    want_type, want_name = resource_addr.split(".", 1)
    m = TF_RESOURCE_RE.search(block_text)
    if not m:
        return False
    return m.group(1) == want_type and m.group(2) == want_name


def _requires_human_context(resolution: str, rule_id: str) -> bool:
    """Return True when a finding *cannot* be resolved automatically because
    the fix requires an organisation-specific value (CIDR range, KMS key ARN,
    IAM role name, certificate ARN, etc.) that no heuristic or LLM can guess
    correctly.

    This runs **before** we spend an LLM call on the finding.  When True,
    the finding should be routed to ``needs_review`` instead of attempted.
    """
    r = resolution.lower()
    # ---- direct value-placeholder patterns ----
    if re.search(r"\bcidr\b", r):
        return True
    if re.search(r"\b(?:kms|cmk)\b.*\b(?:key|arn)\b", r):
        return True
    if re.search(r"\biam\b.*\b(?:role|policy|arn)\b", r):
        return True
    if re.search(r"\b(?:certificate|acm)\b.*\barn\b", r):
        return True
    if re.search(r"\bkey\s*pair\b", r):
        return True
    # ---- deliberate vagueness markers ----
    if re.search(r"\b(?:choose|select|determine|decide)\b", r):
        return True
    if re.search(r"\b(?:organization|account)[- ]specific\b", r):
        return True
    if re.search(r"\b(?:your|specific|appropriate)\b.*\b(?:range|value|cidr|port|key|arn)\b", r):
        return True
    if re.search(r"\bmore\s+restrictive\b", r):
        return True
    if re.search(r"\brestrict\b.*\b(?:to|the)\b", r):
        return True
    # ---- rule-ID-level overrides (rules that are ALWAYS human) ----
    # Trivy may report IDs with or without the "AVD-" prefix depending on
    # version and scan type, so match against the bare suffix.
    _ALWAYS_HUMAN_RULES = {
        "AWS-0104",  # restrict CIDR
        "AWS-0106",  # restrict ingress
        "AWS-0107",  # security group description (human writes it)
        "AWS-0127",  # customer-managed KMS key
    }
    if rule_id in _ALWAYS_HUMAN_RULES or rule_id.removeprefix("AVD-") in _ALWAYS_HUMAN_RULES:
        return True
    return False


def _resolve_tf_dir(file_path: str) -> str | None:
    """Walk upward from *file_path* until we find an initialized terraform
    root (the directory that contains ``.terraform/modules/modules.json``).
    Returns None when no such directory exists up to the filesystem root."""
    d = os.path.dirname(os.path.abspath(file_path))
    while d and d != os.path.dirname(d):  # stop at filesystem root
        if os.path.isfile(os.path.join(d, ".terraform", "modules", "modules.json")):
            return d
        d = os.path.dirname(d)
    return None


def _apply_fix(file_path: str, issue: dict) -> tuple[str, int] | None:
    content = _read_file(file_path)
    if content is None:
        return None

    blocks_before = _parse_resource_blocks(content)
    before_count = len(blocks_before)
    before_addrs = {(t, n) for t, n, _s, _e in blocks_before}

    block = _resolve_block(content, issue.get("resource"), issue.get("start_line"),
                            issue.get("end_line"), issue.get("resource_type"))
    if block is None:
        return None

    resolution = issue.get("resolution", "")
    cheap = _try_cheap_regex_fix(content, block, resolution)
    if cheap:
        new_content, desc = cheap
        if new_content != content and _write_file(file_path, new_content):
            return desc, 0
        return None

    start, end = block
    lines = content.splitlines()
    block_text = "\n".join(lines[start - 1 : end])

    new_block = _llm_fix_block(block_text, issue)
    if not new_block or new_block.strip() == block_text.strip():
        return None

    resource_addr = issue.get("resource") or ""
    if not resource_addr or resource_addr.startswith("module."):
        m = TF_RESOURCE_RE.search(block_text)
        if m:
            resource_addr = f"{m.group(1)}.{m.group(2)}"

    if not _block_matches_resource(new_block, resource_addr):
        print(f"  ⚠ LLM output for {issue['rule_id']} doesn't match expected resource, rejecting")
        return None

    new_block_lines = new_block.splitlines()
    delta = len(new_block_lines) - (end - start + 1)
    new_lines = lines[: start - 1] + new_block_lines + lines[end:]
    new_content = "\n".join(new_lines)

    # HARD CHECK 1: resource count and addresses must be identical after the
    # edit. A splice that creates a duplicate or drops a resource is caught
    # here, deterministically, BEFORE anything is written to disk.
    blocks_after = _parse_resource_blocks(new_content)
    after_count = len(blocks_after)
    after_addrs = {(t, n) for t, n, _s, _e in blocks_after}

    if after_count != before_count:
        print(f"  ✗ REJECTED {issue['rule_id']}: resource count changed "
              f"({before_count} → {after_count}) — refusing to write, no partial edit applied")
        return None

    if before_addrs != after_addrs:
        diff = before_addrs.symmetric_difference(after_addrs)
        print(f"  ✗ REJECTED {issue['rule_id']}: resource addresses changed — {diff} — refusing to write")
        return None

    # HARD CHECK 2: every top-level attribute the LLM wrote must exist in
    # the real AWS provider schema.  Catches hallucinated attribute names
    # (e.g. ``enable_iam_database_authentication`` vs the real
    # ``iam_database_authentication_enabled``) deterministically.
    tf_dir = _resolve_tf_dir(file_path)
    new_resource_match = TF_RESOURCE_RE.search(new_block)
    if tf_dir and new_resource_match:
        valid_attrs = _valid_attributes_for(tf_dir, new_resource_match.group(1))
        if valid_attrs is not None:
            invalid_attrs = _find_invalid_attributes(new_block, valid_attrs)
            if invalid_attrs:
                print(f"  ✗ REJECTED {issue['rule_id']}: unknown attribute(s) "
                      f"{invalid_attrs} for {new_resource_match.group(1)} — "
                      f"not a valid provider argument, refusing to write")
                return None

    if not _write_file(file_path, new_content):
        return None
    return f"LLM rewrote block for: {issue['title'][:70]}", delta



# ==========================================
# VALIDATE + REPAIR (not revert)
# ==========================================


def _is_terraform_initialized(tf_dir: str) -> bool:
    modules_json = os.path.join(tf_dir, ".terraform", "modules", "modules.json")
    return os.path.isfile(modules_json)


def _terraform_validate(tf_dir: str) -> tuple[bool, list[dict]]:
    """Run `terraform validate -json` and return (valid, diagnostics)."""
    try:
        result = subprocess.run(
            ["terraform", "validate", "-json"],
            cwd=tf_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout:
            return False, [{"summary": "terraform validate produced no output", "detail": result.stderr}]
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, OSError) as e:
        return False, [{"summary": f"terraform validate failed to run: {e}", "detail": ""}]

    return bool(parsed.get("valid", False)), parsed.get("diagnostics", [])


def _llm_fix_syntax(file_content: str, error_text: str) -> str | None:
    """Ask the LLM to repair validation errors using the full file, since a
    syntax error can affect parsing beyond the block that was edited."""
    system_prompt = (
        "You are a Terraform syntax repair assistant. You are given the full "
        "contents of a .tf file and the exact `terraform validate` errors it "
        "produced. Fix ONLY what is necessary to resolve these errors — do not "
        "change resource logic, attribute values, or formatting beyond what the "
        "errors require, and do not remove any resource blocks. Return the "
        "complete corrected file inside a single ```hcl code block, with no "
        "other commentary."
    )
    user_prompt = f"Validation errors:\n{error_text}\n\nFile contents:\n```hcl\n{file_content}\n```"
    try:
        llm = _get_llm()
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
    except Exception as e:
        print(f"  ⚠ LLM syntax repair call failed: {e}")
        return None

    return _extract_hcl_block(getattr(response, "content", "") or "")


def _ensure_terraform_initialized(tf_dir: str) -> bool:
    """Idempotent: returns True if the dir is (or becomes) initialized."""
    if _is_terraform_initialized(tf_dir):
        return True

    result = subprocess.run(
        ["terraform", "init", "-input=false", "-no-color", "-backend=false"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"  ⚠ terraform init failed: {result.stderr[:500]}")
        return False
    return True

def _repair_syntax_errors(tf_dir: str) -> bool:
    """Validate the terraform dir; if invalid, feed the exact diagnostics
    back to the LLM per affected file and retry, up to
    MAX_SYNTAX_REPAIR_ATTEMPTS times. Never reverts — always tries to move
    the files forward to a valid state instead of discarding the fix."""
    if not _ensure_terraform_initialized(tf_dir):   
        print(
            "\n  ┌─────────────────────────────────────────────────────────────┐\n"
            "  │  WARNING: .terraform/ not found                              │\n"
            "  │  Syntax validation + LLM repair is SKIPPED this run.         │\n"
            "  │  Any syntax errors introduced by fixes will go undetected.   │\n"
            "  │  Run `terraform init` in the target dir before re-running.   │\n"
            "  └─────────────────────────────────────────────────────────────┘\n"
        )
        return False  # validation was skipped, not passed

    for attempt in range(1, MAX_SYNTAX_REPAIR_ATTEMPTS + 1):
        valid, diagnostics = _terraform_validate(tf_dir)
        if valid:
            if attempt > 1:
                print(f"  ✓ terraform validate passed after {attempt - 1} repair attempt(s).")
            return True

        print(f"  ⚠ terraform validate failed (repair attempt {attempt}/{MAX_SYNTAX_REPAIR_ATTEMPTS})")

        by_file: dict[str, list[dict]] = {}
        global_diags: list[dict] = []
        for d in diagnostics:
            rng = d.get("range") or {}
            fname = rng.get("filename")
            if fname:
                by_file.setdefault(fname, []).append(d)
            else:
                global_diags.append(d)

        if not by_file:
            print("  ⚠ All validation errors are non-file-scoped (provider / backend level):")
            for d in global_diags:
                detail = d.get("detail", "") or ""
                print(f"     - {d.get('summary', '?')}: {detail[:120]}")
            return False

        # Non-file-scoped diagnostics (provider, backend) carry context the
        # LLM needs — surface them alongside file-specific errors.
        global_context = ""
        if global_diags:
            global_context = "\n".join(
                f"- [global] {d.get('summary', '')} — {d.get('detail', '')}"
                for d in global_diags
            )

        for fname, diags in by_file.items():
            fpath = _resolve_file_path(tf_dir, fname)
            if not fpath:
                print(f"  ⚠ Could not locate file '{fname}' to repair")
                continue

            content = _read_file(fpath)
            if content is None:
                continue

            blocks_before = _parse_resource_blocks(content)

            error_text = "\n".join(
                f"- line {d.get('range', {}).get('start', {}).get('line', '?')}: "
                f"{d.get('summary', '')} — {d.get('detail', '')}"
                for d in diags
            )
            if global_context:
                error_text = global_context + "\n" + error_text

            fixed = _llm_fix_syntax(content, error_text)
            if not fixed or fixed.strip() == content.strip():
                print(f"  ⏭  No repair produced for {os.path.basename(fpath)}")
                continue

            blocks_after = _parse_resource_blocks(fixed)
            # Verify every resource address that existed before the repair
            # still exists after.  A simple count check isn't enough — the
            # LLM could silently replace one resource with a different one
            # and the counts would match.
            before_addrs = {(t, n) for t, n, _s, _e in blocks_before}
            after_addrs  = {(t, n) for t, n, _s, _e in blocks_after}
            missing = before_addrs - after_addrs
            if missing:
                print(
                    f"  ⚠ Repair for {os.path.basename(fpath)} would drop or alter "
                    f"{len(missing)} resource(s): {sorted(missing)} — rejecting, not written"
                )
                continue

            if _write_file(fpath, fixed):
                print(f"  🔧 Repaired syntax in {os.path.basename(fpath)}")

    valid, _ = _terraform_validate(tf_dir)
    if not valid:
        print(
            f"  ✗ Still invalid after {MAX_SYNTAX_REPAIR_ATTEMPTS} repair attempt(s) — "
            "proceeding anyway, next Trivy scan will likely surface this too."
        )
    return valid


# ==========================================
# LANGGRAPH NODES
# ==========================================


def scan_terraform(state: State) -> dict:
    """Run Trivy and store scan results + extracted issues."""
    tf_dir = state["tf_dir"]
    iteration = state["iteration"]

    print(f"[iter {iteration + 1}] Scanning with Trivy …")

    raw = _run_trivy(tf_dir)

    if "error" in raw:
        print(f"  ⚠ Error: {raw['error']}")
        return {
            "messages": [f"Trivy error: {raw['error']}"],
            "passed": True,
            "trivy_error": True,
            "issues": [],
            "scan_results": [],
        }

    if not raw or "Results" not in raw:
        print(f"  ⚠ Trivy returned no Results key (raw keys: {list(raw.keys())})")
        return {
            "messages": ["Trivy returned no results — possible config issue"],
            "passed": True,
            "trivy_error": True,
            "issues": [],
            "scan_results": [],
        }

    issues = _extract_issues(raw, tf_dir)
    passed = len(issues) == 0

    if passed:
        print(f"[iter {iteration + 1}] ✓ No issues found.\n")
    else:
        severity_counts: dict[str, int] = {}
        for i in issues:
            sev = i["severity"]
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        counts = ", ".join(f"{k}={v}" for k, v in sorted(severity_counts.items()))
        print(f"[iter {iteration + 1}] ✗ {len(issues)} issue(s) found ({counts})")
        for i in issues:
            resource = i.get('resource') or os.path.basename(i.get('target', '') or '?')
            print(f"    {i['severity']:8s}  {i['rule_id']:12s}  {resource}")
        print()

    return {
        "scan_results": raw.get("Results", []),
        "issues": issues,
        "passed": passed,
        "trivy_error": False,
        "messages": [f"Scan complete: {len(issues)} issues"],
    }


def fix_issues(state: State) -> dict:
    issues = state["issues"]
    tf_dir = state["tf_dir"]
    iteration = state["iteration"]
    max_iter = state["max_iterations"]
    fixes: list[FixEntry] = []

    if not issues:
        return {"iteration": iteration + 1, "fixes_applied": []}

    ordered_issues = sorted(
        issues, key=lambda i: _SEVERITY_RANK.get((i.get("severity") or "UNKNOWN").upper(), 4)
    )

    # Partition: findings that need a human decision (CIDR range, KMS ARN,
    # IAM role, etc.) go to needs_review instead of the fix loop.
    auto_fixable: list[dict] = []
    needs_review: list[dict] = []
    for issue in ordered_issues:
        resolution = issue.get("resolution", "")
        if resolution and _requires_human_context(resolution, issue["rule_id"]):
            needs_review.append(issue)
        else:
            auto_fixable.append(issue)

    if needs_review:
        print(f"  ⚠ {len(needs_review)} finding(s) need a human decision "
              f"(CIDR, KMS ARN, IAM role, etc.) — will not attempt auto-fix:\n")
        for issue in needs_review:
            resource = issue.get('resource') or os.path.basename(issue.get('target', '') or '?')
            print(f"      {issue['rule_id']}  on  {resource}")
            print(f"      → {issue['resolution'][:140]}")
            print(f"      Suppress: # trivy:ignore:{issue['rule_id']} -- {issue['resolution'][:80]}")
            print()

    file_line_offsets: dict[str, int] = {}

    for issue in auto_fixable:
        rule_id = issue["rule_id"]
        resolution = issue.get("resolution", "")

        if not resolution:
            print(f"  ⏭  {rule_id}: no resolution text, skipping")
            continue

        file_path = _resolve_file_path(tf_dir, issue.get("target", ""))
        if not file_path:
            print(f"  ⏭  {rule_id}: could not locate file for target '{issue.get('target')}'")
            continue

        offset = file_line_offsets.get(file_path, 0)
        adjusted_issue = issue
        if offset and issue.get("start_line") is not None and issue.get("end_line") is not None:
            adjusted_issue = dict(issue)
            adjusted_issue["start_line"] = issue["start_line"] + offset
            adjusted_issue["end_line"] = issue["end_line"] + offset

        result = _apply_fix(file_path, adjusted_issue)
        if result:
            desc, delta = result
            file_line_offsets[file_path] = offset + delta
            fixes.append(FixEntry(rule_id=rule_id, file_path=file_path, description=desc))
            print(f"  ✓ {rule_id}: {desc}  ({os.path.basename(file_path)}, {issue.get('resource')})")
        else:
            print(f"  ⏭  {rule_id}: no applicable fix  ({resolution[:80]})")

    if fixes:
        _repair_syntax_errors(tf_dir)

    # When every remaining issue requires a human decision (CIDR range,
    # KMS ARN, etc.), there is nothing the agent can do — force the loop
    # to exit by saturating the iteration counter, so should_continue
    # routes to END immediately instead of burning max_iterations cycles.
    next_iteration = max_iter if (not auto_fixable and not fixes) else iteration + 1
    return {"iteration": next_iteration, "fixes_applied": fixes}



def should_continue(state: State) -> Literal["fix_issues", "__end__"]:
    """Decide whether to loop back to fix/scan or exit."""
    if state["passed"]:
        return "__end__"
    if state["iteration"] >= state["max_iterations"]:
        return "__end__"
    return "fix_issues"


# ==========================================
# BUILD THE GRAPH
# ==========================================

workflow = StateGraph(State)

workflow.add_node("scan_terraform", scan_terraform)
workflow.add_node("fix_issues", fix_issues)

workflow.add_edge(START, "scan_terraform")
workflow.add_conditional_edges(
    "scan_terraform",
    should_continue,
    {"fix_issues": "fix_issues", "__end__": END},
)
workflow.add_conditional_edges(
    "fix_issues",
    lambda state: "__end__" if state["iteration"] >= state["max_iterations"] else "scan_terraform",
    {"scan_terraform": "scan_terraform", "__end__": END},
)

graph = workflow.compile()


# ==========================================
# CLI ENTRYPOINT
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="LangGraph agent: scan tf code with Trivy until it passes."
    )
    parser.add_argument(
        "--tf-dir",
        default=r"D:\aws-terraform-drift-reconciler\test\ec2_terraform",
        help="Path to the terraform directory to scan",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Maximum scan-fix cycles before giving up",
    )
    args = parser.parse_args()

    tf_dir = os.path.abspath(args.tf_dir)

    if not os.path.isdir(tf_dir):
        print(f"Error: directory not found — {tf_dir}")
        sys.exit(1)

    if not shutil.which("trivy"):
        print("Error: trivy CLI is not installed or not in PATH.")
        sys.exit(1)

    if not shutil.which("terraform"):
        print("Error: terraform CLI is not installed or not in PATH.")
        sys.exit(1)

    if not _is_terraform_initialized(tf_dir):
        print(f"Note: '{tf_dir}' has no .terraform/ — syntax validate/repair will be skipped this run.")

    print(f"Terraform dir: {tf_dir}")
    print(f"Max iterations: {args.max_iterations}")
    print()

    initial: State = {
        "tf_dir": tf_dir,
        "scan_results": [],
        "issues": [],
        "fixes_applied": [],
        "iteration": 0,
        "max_iterations": args.max_iterations,
        "passed": False,
        "trivy_error": False,
        "messages": [],
    }

    final_state = graph.invoke(initial)

    print()
    if final_state["passed"]:
        if final_state.get("trivy_error"):
            print("✗ Trivy encountered an error — scan did not complete successfully.")
        else:
            print("✓ All Trivy checks pass!")
    else:
        remaining = final_state["issues"]
        human = [i for i in remaining
                 if i.get("resolution") and _requires_human_context(i["resolution"], i["rule_id"])]
        auto = [i for i in remaining if i not in human]
        if auto:
            print(
                f"✗ {len(auto)} auto-fixable issue(s) could not be resolved "
                f"after {final_state['iteration']} iteration(s)."
            )
        if human:
            print(
                f"🔍 {len(human)} issue(s) require human review "
                f"(CIDR range, KMS ARN, IAM role, etc.) — cannot be auto-fixed.\n"
            )
            for issue in human:
                print(f"    # trivy:ignore:{issue['rule_id']} -- {issue['resolution'][:80]}")

    if final_state["fixes_applied"]:
        print(f"\nFixes applied ({len(final_state['fixes_applied'])}):")
        for fix in final_state["fixes_applied"]:
            print(f"  • [{fix['rule_id']}] {fix['description']}  ({fix['file_path']})")


if __name__ == "__main__":
    main()