"""Shared helpers used across drift-reconciler modules."""


def mask_secret(val) -> str | None:
    """Return a masked version of *val*, or None if empty."""
    if not val:
        return None
    s = str(val)
    if len(s) <= 4:
        return "••••"
    return "••••" + s[-4:]
