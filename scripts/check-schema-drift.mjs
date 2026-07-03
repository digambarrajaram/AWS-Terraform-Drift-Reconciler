import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert, loadJson, readText } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const policyPath = path.join(repoRoot, 'drift-controls', 'schema-policy.json');

async function main() {
  const policy = loadJson(policyPath);
  const violations = [];

  for (const entry of policy.migrationChecks) {
    const filePath = path.join(repoRoot, entry.file);
    const content = readText(filePath);
    if (!content.includes(entry.mustContain)) {
      violations.push({ file: entry.file, mustContain: entry.mustContain });
    }
  }

  if (violations.length > 0) {
    console.error('[schema-drift] Schema migration drift detected.');
    await emitDriftAlert('Schema drift detected', { policyPath, violations }, 'high');
    process.exit(1);
  }

  console.log('[schema-drift] No schema drift detected.');
}

main().catch((error) => {
  console.error(`[schema-drift] ${error.message}`);
  process.exit(1);
});
