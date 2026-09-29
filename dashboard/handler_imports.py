"""Safe lazy imports for HTTP handlers (ImportError → 500 JSON + log)."""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)


def import_module_or_json_error(handler, module: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        logger.exception("Failed to import %s", module)
        handler._json_error(500, str(exc))
        return None


def import_attr_or_json_error(handler, module: str, attr: str):
    mod = import_module_or_json_error(handler, module)
    if mod is None:
        return None
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        logger.exception("%s has no attribute %s", module, attr)
        handler._json_error(500, str(exc))
        return None
