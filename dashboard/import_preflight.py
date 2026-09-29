"""Import every dashboard / drift_reconciler module once at process startup."""
from __future__ import annotations

import importlib
import pkgutil


def _submodule_names(package: str) -> list[str]:
    pkg = importlib.import_module(package)
    if not getattr(pkg, "__path__", None):
        return [package]
    names = [package]
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        names.append(info.name)
    return sorted(set(names))


def verify_package_imports() -> None:
    """Import all package modules; raise SystemExit listing any broken import."""
    failures: list[str] = []
    for package in ("drift_reconciler", "dashboard"):
        for name in _submodule_names(package):
            try:
                importlib.import_module(name)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
    if failures:
        lines = ["Import preflight failed — fix broken imports before serving:"]
        lines.extend(f"  - {item}" for item in failures)
        raise SystemExit("\n".join(lines))
