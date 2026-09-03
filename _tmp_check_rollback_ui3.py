import pathlib
import re

root = pathlib.Path(".")
fe = (root / "frontend/artifacts/web/src/pages/Rollback.tsx").read_text(encoding="utf-8")
hook = (root / "frontend/artifacts/web/src/hooks/useRollbackData.ts").read_text(encoding="utf-8")
be = (root / "drift_reconciler/rollback_flow.py").read_text(encoding="utf-8")

print("=== hook current/original/fixed lines ===")
for line in hook.splitlines():
    if any(k in line for k in ("current", "original", "fixed", "field", "resource")):
        print(repr(line))

print("FE current_* :", sorted(set(re.findall(r"current_\w+", fe))))
print("BE current_* :", sorted(set(re.findall(r"current_\w+", be))))
print("FE funcs:", re.findall(r"^function (\w+)", fe, re.M))

for c in [
    "EligiblePRList",
    "EligiblePRList",
    "DiffTable",
    "DiffTable",
    "RollbackStageIndicator",
    "RollbackStageIndicator",
    "LogViewer",
    "LogViewer",
    "Ban",
    "ClipboardCheck",
    "AlertTriangle",
]:
    print(f"{c}: def={('function ' + c) in fe} jsx/use={c in fe}")

m = re.search(r"interface ActiveCtx \{([\s\S]*?)\}", fe)
print("ActiveCtx fields:", m.group(1) if m else None)
print("ctx. usages:", sorted(set(re.findall(r"ctx\.(\w+)", fe))))
print("setCtx error keys:", sorted(set(re.findall(r"(error\w+|failed\w+):", fe))))

# Diff table cell rendering
dt = fe[fe.find("function DiffTable") : fe.find("function RollbackHistory")]
print("DiffTable truncate?", "truncate" in dt)
print("DiffTable pre-wrap?", "pre-wrap" in dt)
print("DiffTable title attrs?", "title=" in dt)

# Page intro
print("page subtitle/help?", "Eligible PRs" in fe)
print("how many h2:", len(re.findall(r"<h2", fe)))
