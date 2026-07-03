import { EC2Client, DescribeInstancesCommand } from '@aws-sdk/client-ec2';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert, loadJson, readText } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const policyPath = path.join(repoRoot, 'drift-controls', 'operational-policy.json');

async function main() {
  const policy = loadJson(policyPath);
  const region = process.env.AWS_REGION || 'us-east-1';
  if (!process.env.AWS_ACCESS_KEY_ID || !process.env.AWS_SECRET_ACCESS_KEY) {
    console.log('[operational-drift] AWS credentials not configured; skipping operational drift check.');
    return;
  }

  const client = new EC2Client({ region });
  const response = await client.send(new DescribeInstancesCommand({}));
  const reservations = response.Reservations || [];
  const instances = reservations.flatMap((reservation) => reservation.Instances || []);

  const violations = [];
  for (const instance of instances) {
    const state = instance.State?.Name || 'unknown';
    const tags = Object.fromEntries((instance.Tags || []).map((tag) => [tag.Key, tag.Value]));
    if (state !== policy.requiredState && !tags.DriftPolicy?.includes('ignore')) {
      violations.push({ instanceId: instance.InstanceId, state, expected: policy.requiredState });
    }
  }

  if (instances.length < policy.minHealthyInstances) {
    violations.push({ instanceCount: instances.length, expectedMin: policy.minHealthyInstances });
  }

  if (violations.length > 0) {
    const details = { policyPath, violations };
    console.error('[operational-drift] Operational policy violations detected.');
    await emitDriftAlert('Operational drift detected', details, 'high');
    process.exit(1);
  }

  console.log('[operational-drift] No operational drift detected.');
}

main().catch((error) => {
  console.error(`[operational-drift] ${error.message}`);
  process.exit(1);
});
