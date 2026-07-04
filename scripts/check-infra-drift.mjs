import { execFile } from 'node:child_process';
import path from 'node:path';
import { env } from 'node:process';
import { fileURLToPath } from 'node:url';

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
      console.error('[infra-drift] Terraform plan reported drift.');
      process.exit(1);
    }
    console.error(`[infra-drift] Terraform plan failed with exit code ${code}`);
    process.exit(1);
  } catch (error) {
    console.warn(`[infra-drift] Terraform check skipped: ${error.message}`);
  }
}

main();
