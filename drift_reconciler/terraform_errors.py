"""Terraform/rollback error humanizers."""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def humanize_terraform_error(raw_error: str) -> dict:
    """Return ``{summary, detail, suggestion}`` for a terraform error."""
    text = raw_error.lower() if raw_error else ""

    patterns = [
        (("terraform init failed", "error installing", "failed to install provider",
          "failed to query provider", "could not download", "provider registry"), {
            "summary": "Terraform init failed — the unmanaged scan could not load state.",
            "suggestion": (
                "Check network access to the Terraform provider registry and "
                "that the environment's state backend is reachable, then re-run "
                "the scan. A green 'no findings' result is not valid until init succeeds."
            ),
        }),
        (("nosuchbucket", "does not exist"), {
            "summary": "The Terraform state backend for this scope isn't set up yet.",
            "suggestion": "Confirm the S3 state bucket exists for this account/region before scanning.",
        }),
        (("invalidclienttokenid", "expiredtoken", "unrecognizedclientexception"), {
            "summary": "AWS credentials for this scope are invalid or expired.",
            "suggestion": "Check the IAM credentials/role configured for this scope.",
        }),
        (("accessdenied", "access denied"), {
            "summary": "The configured AWS credentials don't have permission to read this scope's infrastructure.",
            "suggestion": "Check IAM permissions for the scan role.",
        }),
        (("connection refused", "timeout", "could not connect", "i/o timeout", "context deadline"), {
            "summary": "Couldn't reach AWS or the Terraform backend — possible network issue.",
            "suggestion": "Check network connectivity and try again.",
        }),
        (("profilenotfound", "profile", "could not be found"), {
            "summary": "The AWS profile configured for this environment doesn't exist on this machine.",
            "suggestion": "Create the AWS named profile in ~/.aws/config, or update the environment's profile via the Environments page.",
        }),
    ]

    for keywords, info in patterns:
        if any(kw in text for kw in keywords):
            return {"summary": info["summary"], "detail": raw_error, "suggestion": info["suggestion"]}

    return {
        "summary": "The Terraform plan failed with an unrecognised error.",
        "detail": raw_error,
        "suggestion": "See technical details below.",
    }


def humanize_rollback_error(raw_error: str) -> dict:
    """Return ``{summary, detail, suggestion}`` for a rollback error.

    Same contract as ``humanize_terraform_error`` — *summary* is the
    user-facing message, *detail* is the raw technical error kept for
    debugging, and *suggestion* offers a concrete next step."""
    text = raw_error.lower() if raw_error else ""

    patterns = [
        (("no baselines found for pr #",), {
            "summary": (
                "This PR doesn't have a recorded baseline yet — this "
                "usually means the PR hasn't been merged, or drift "
                "tracking wasn't recording state when it was created. "
                "A rollback isn't possible until a baseline exists."
            ),
            "suggestion": (
                "Verify the PR was merged (not just closed) and that "
                "the drift-reconciler pipeline ran successfully when it "
                "was created. If the PR predates baseline recording, a "
                "manual rollback with terraform apply may be needed."
            ),
        }),
        (("no resources passed freshness check",), {
            "summary": (
                "None of the resources in this PR still show the drift "
                "that the original fix addressed — the live AWS state "
                "already matches the rollback target."
            ),
            "suggestion": (
                "This usually means the original drift was independently "
                "resolved (e.g. a manual terraform apply or console change). "
                "No rollback is needed."
            ),
        }),
        (("source .tf file not found",), {
            "summary": (
                "The Terraform source file referenced in the baseline "
                "no longer exists on disk in this environment."
            ),
            "suggestion": (
                "Check whether the file was moved, renamed, or deleted "
                "since the original PR was created. If the resource was "
                "intentionally removed, the baseline is stale and this "
                "rollback can be disregarded."
            ),
        }),
        (("terraform plan failed", "terraform plan timed out"), {
            "summary": (
                "Could not connect to AWS to verify current resource "
                "state — the Terraform plan step failed."
            ),
            "suggestion": (
                "Check AWS credentials and network connectivity for this "
                "scope, then retry. If the issue persists, the state "
                "backend (S3/DynamoDB) may be temporarily unavailable."
            ),
        }),
    ]

    for keywords, info in patterns:
        if all(kw in text for kw in keywords):
            return {"summary": info["summary"], "detail": raw_error, "suggestion": info["suggestion"]}

    return {
        "summary": "The rollback failed with an unrecognised error.",
        "detail": raw_error,
        "suggestion": "See the technical details below for the raw error.",
    }


# ==========================================
# 1. RUN TERRAFORM & DRIFT SCRIPTS
# ==========================================
