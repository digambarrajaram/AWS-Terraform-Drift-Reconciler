import re
import pathlib

fe = pathlib.Path(r"frontend/artifacts/web/src/pages/Rollback.tsx").read_text(encoding="utf-8")

funcs = re.findall(r"^function (\w+)", fe, re.M)
consts = re.findall(r"^const \[(\w+),", fe, re.M)
print("functions:", funcs)
print("useState names:", consts)

# find JSX component usages that look like custom components
comps = sorted(set(re.findall(r"<([A-Z][A-Za-z0-9]+)", fe)))
print("JSX components:", comps)

# find identifiers near setPhase / handlers
for pat in ["Eligible", "Stage", "Diff", "History", "handleP", "handleE", "confirm", "stale", "setSubmit", "setConfirm"]:
    hits = sorted(set(re.findall(rf"\b(\w*{pat}\w*)\b", fe, re.I)))
    print(pat, "->", hits)

# Print the page return section variable refs that might be wrong
# extract from "export default" onward briefly around Eligible and History
idx = fe.find("export default")
tail = fe[idx:idx+8000]
# show lines with Eligible / History / Diff / Stage / stale / confirm / submit
for i, line in enumerate(tail.splitlines(), 1):
    if any(k in line for k in ["Eligible", "History", "Diff", "Stage", "stale", "confirm", "Submit", "handleP", "handleE", "hist"]):
        print(f"L{i}: {line}")
