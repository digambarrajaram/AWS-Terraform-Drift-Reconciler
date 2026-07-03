import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert, loadJson, sha256, readText } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const baselinePath = path.join(repoRoot, 'drift-controls', 'config-baseline.json');

async function main() {
  const baseline = loadJson(baselinePath);
  const violations = [];

  for (const entry of baseline.files) {
    const absolutePath = path.join(repoRoot, entry.path);
    const content = readText(absolutePath);
    const currentHash = sha256(content);
    const expectedHash = entry.sha256 === '__placeholder__' ? sha256(readText(path.join(repoRoot, entry.path))) : entry.sha256;
    if (currentHash !== expectedHash) {
      violations.push({ path: entry.path, expectedSha256: expectedHash, actualSha256: currentHash });
    }
  }

  if (violations.length > 0) {
    console.error('[config-drift] Configuration baseline drift detected.');
    await emitDriftAlert('Configuration drift detected', { baselinePath, violations }, 'medium');
    process.exit(1);
  }

  console.log('[config-drift] No configuration drift detected.');
}

main().catch((error) => {
  console.error(`[config-drift] ${error.message}`);
  process.exit(1);
});
