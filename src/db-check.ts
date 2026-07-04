import { execFileSync } from 'child_process';
import * as path from 'path';

export interface SchemaDriftReport {
  appliedMigrations: string[];
  pendingMigrations: string[];
  status: 'ok' | 'pending' | 'unknown';
  details?: string;
}

export function checkSchemaDrift(workingDir?: string): SchemaDriftReport {
  const dir = workingDir || process.env.TERRAFORM_DIR || path.join(process.cwd(), 'terraform', 'ec2');
  const prismaBin = process.env.PRISMA_BINARY || 'npx';
  try {
    const result = execFileSync(prismaBin, ['prisma', 'migrate', 'status', '--schema', path.join(dir, 'schema.prisma'), '--json'], {
      cwd: dir,
      encoding: 'utf-8',
      timeout: 15000,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const parsed = JSON.parse(result);
    const applied = parsed?.applied || [];
    const pending = parsed?.pending || [];
    const status: 'ok' | 'pending' = pending.length === 0 ? 'ok' : 'pending';
    return {
      appliedMigrations: applied.map((m: any) => m?.migrationName || m || ''),
      pendingMigrations: pending.map((m: any) => m?.migrationName || m || ''),
      status,
      details: `Prisma migrate status returned ${pending.length} pending migrations.`,
    };
  } catch (err: any) {
    return {
      appliedMigrations: [],
      pendingMigrations: [],
      status: 'unknown',
      details: `Schema drift check failed: ${err.message}`,
    };
  }
}
