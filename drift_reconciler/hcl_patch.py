"""Line-level HCL find-and-replace inside a resource block."""
from __future__ import annotations

import re

TF_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')


def _apply_changes_to_file(file_path: str, resource_addr: str, changes: dict) -> bool:
    """Apply before→after value replacements inside the named resource block.
    Returns True if at least one change was applied."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return False

    if "." not in resource_addr:
        return False
    want_type, want_name = resource_addr.split(".", 1)

    # ponytail: simple line-level find-and-replace inside the resource block.
    # A proper HCL-aware attribute setter would be more robust.
    lines = content.splitlines()
    in_block = False
    depth = 0
    applied = False
    for i, line in enumerate(lines):
        m = TF_RESOURCE_RE.search(line)
        if m and m.group(1) == want_type and m.group(2) == want_name:
            in_block = True
            depth = line.count("{") - line.count("}")
            continue
        if in_block:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
            for field, vals in changes.items():
                before_val = str(vals.get("before", ""))
                after_val = str(vals.get("after", ""))
                if before_val and before_val in line:
                    lines[i] = line.replace(before_val, after_val, 1)
                    applied = True
                    break

    if applied:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError:
            return False
    return applied


