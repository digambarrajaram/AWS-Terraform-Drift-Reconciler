import re
import pathlib

fe = pathlib.Path(r"frontend/artifacts/web/src/pages/Rollback.tsx").read_text(encoding="utf-8")
be = pathlib.Path(r"drift_reconciler/rollback_flow.py").read_text(encoding="utf-8")
hook = pathlib.Path(r"frontend/artifacts/web/src/hooks/useRollbackData.ts").read_text(encoding="utf-8")

fe_keys = re.findall(r"key: '([^']+)'", fe)
be_keys = sorted(set(re.findall(r'_report_rollback_stage\([^,]+,\s*"([^"]+)"\)', be)))
print("FE stages:", fe_keys)
print("BE stages:", be_keys)
print("mismatch:", set(be_keys) - set(fe_keys), "extra FE:", set(fe_keys) - set(be_keys))

m = re.search(r"export interface PreviewDiffRow \{[\s\S]*?\}", hook)
print("hook PreviewDiffRow:\n", m.group(0) if m else "NOT FOUND")

print("row.* in Diff area:", sorted(set(re.findall(r"row\.(\w+)", fe))))
print("RotateCcw in imports:", "RotateCcw" in fe.split("} from 'lucide-react'")[0])
print("uses RotateCcw:", "RotateCcw" in fe)
print("uses format(:", bool(re.search(r"(?<![A-Za-z])format\(", fe)))
print("imports format:", "format," in fe or "format }" in fe or "{ format" in fe)

# broken identifiers that would crash
for name in ["RotateCcw", "EligiblePRList", "RollbackStageIndicator", "DiffTable",
             "RollbackHistory", "handlePreview", "confirmOpen", "histModeFilter",
             "setConfirmOpen", "setSubmitting", "staleCount"]:
    print(f"  {name}: defined={bool(re.search(rf'(function|const) {name}\\b|{name},|{name}\\s*=', fe))} used={fe.count(name)}")

# status values
print("FE RUN_STATUS keys:", re.findall(r"^\s+(\w+):\s+\{\s*label:", fe, re.M))
print("hook status union:", re.search(r"status:\s*'[^']+'(?:\s*\|\s*'[^']+')*", hook))
print("hook mode field?", "mode:" in hook, "mode?" )
print("hook current_stage?", "current_stage" in hook)
print("FE current_stage usage:", "current_stage" in fe, "currentStage" in fe)
