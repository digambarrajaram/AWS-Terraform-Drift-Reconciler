"""Shared helpers used across drift-reconciler modules."""

import re

# GitHub PAT prefixes (classic, fine-grained, OAuth, user/server tokens).
_GITHUB_PAT_RE = re.compile(
    r"^(?:"
    r"ghp_[A-Za-z0-9]{36}|"
    r"github_pat_[A-Za-z0-9_]{22,}|"
    r"gho_[A-Za-z0-9]{36}|"
    r"ghu_[A-Za-z0-9]{36}|"
    r"ghs_[A-Za-z0-9]{36}|"
    r"ghr_[A-Za-z0-9]{36}"
    r")$"
)


def is_valid_github_pat(token: str) -> bool:
    """Return True when *token* matches a known GitHub PAT format."""
    return bool(_GITHUB_PAT_RE.fullmatch((token or "").strip()))


def mask_secret(val) -> str | None:
    """Return a masked version of *val*, or None if empty."""
    if not val:
        return None
    s = str(val)
    if len(s) <= 4:
        return "••••"
    return "••••" + s[-4:]
