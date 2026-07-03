import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert, loadJson, readText } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const policyPath = path.join(repoRoot, 'drift-controls', 'environment-policy.json');

async function main() {
  const policy = loadJson(policyPath);
  const violations = [];

  for (const entry of policy.environments) {
    const envPath = path.join(repoRoot, entry.file);
    const content = readText(envPath);
    const expected = entry.expected;
    if (!content.includes(expected)) {
      violations.push({ environment: entry.name, file: entry.file, expected });
    }
  }

  if (violations.length > 0) {
    console.error('[environment-drift] Environment parity drift detected.');
    await emitDriftAlert('Environment drift detected', { policyPath, violations }, 'medium');
    process.exit(1);
  }

  console.log('[environment-drift] No environment drift detected.');
}

main().catch((error) => {
  console.error(`[environment-drift] ${error.message}`);
  process.exit(1);
});
