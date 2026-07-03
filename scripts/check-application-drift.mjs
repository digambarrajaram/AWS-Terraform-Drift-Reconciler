import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert, loadJson, readText } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const policyPath = path.join(repoRoot, 'drift-controls', 'application-policy.json');
const releasePath = path.join(repoRoot, 'drift-controls', 'application-release.json');

async function main() {
  const policy = loadJson(policyPath);
  const release = loadJson(releasePath);
  const violations = [];

  if (release.sourceBranch !== policy.requiredBranch) {
    violations.push({ field: 'sourceBranch', expected: policy.requiredBranch, actual: release.sourceBranch });
  }
  if (release.deploymentMode !== policy.requiredDeploymentMode) {
    violations.push({ field: 'deploymentMode', expected: policy.requiredDeploymentMode, actual: release.deploymentMode });
  }
  if (!release.artifactDigest || release.artifactDigest.length < 10) {
    violations.push({ field: 'artifactDigest', expected: 'non-empty digest', actual: release.artifactDigest || 'missing' });
  }

  if (violations.length > 0) {
    console.error('[application-drift] Application release drift detected.');
    await emitDriftAlert('Application drift detected', { policyPath, releasePath, violations }, 'high');
    process.exit(1);
  }

  console.log('[application-drift] No application drift detected.');
}

main().catch((error) => {
  console.error(`[application-drift] ${error.message}`);
  process.exit(1);
});
