import { execFile } from 'node:child_process';
import path from 'node:path';
import { env } from 'node:process';
import { fileURLToPath } from 'node:url';
import { emitDriftAlert } from './drift-alert.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const terraformDir = env.TERRAFORM_DIR || path.join(repoRoot, 'terraform', 'ec2');

function runTerraformPlan() {
  return new Promise((resolve) => {
    const terraformPath = env.TERRAFORM_PATH || 'terraform';
    execFile(terraformPath, ['plan', '-no-color', '-input=false', '-detailed-exitcode'], { cwd: terraformDir }, (error, stdout, stderr) => {
      const code = error && typeof error.code === 'number' ? error.code : 0;
      resolve({ code, stdout, stderr });
    });
  });
}

async function main() {
  try {
    const { code, stdout, stderr } = await runTerraformPlan();
    if (code === 0) {
      console.log('[infra-drift] No infrastructure drift detected.');
      return;
    }
    if (code === 2) {
      const details = { terraformDir, stdout: stdout.slice(0, 2000), stderr: stderr.slice(0, 2000) };
      console.error('[infra-drift] Terraform plan reported drift.');
      await emitDriftAlert('Infrastructure drift detected', details, 'high');
      process.exit(1);
    }
    console.error(`[infra-drift] Terraform plan failed with exit code ${code}`);
    await emitDriftAlert('Infrastructure drift check failed', { terraformDir, stderr }, 'high');
    process.exit(1);
  } catch (error) {
    console.warn(`[infra-drift] Terraform check skipped: ${error.message}`);
  }
}

main();
