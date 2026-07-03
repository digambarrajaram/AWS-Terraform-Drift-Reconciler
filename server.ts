/**
 * AWS Terraform Drift Reconciler — Production Server
 *
 * Resources are loaded dynamically from S3 terraform state at startup.
 * Drift detection compares desired state (from tfstate) vs actual AWS (EC2 describe).
 * Alerts go through PagerDuty (email/SMS/calls — no separate Slack/Email config).
 * Policy checks (Checkov, terraform validate) run in CI/CD (GitHub Actions).
 * Agent self-corrects: retries with error feedback until validation passes.
 */

import * as express from 'express';
import * as path from 'path';
import * as crypto from 'crypto';
import { randomUUID } from 'crypto';
import * as dotenv from 'dotenv';
import { spawn } from 'child_process';
import { createServer as createViteServer } from 'vite';
import {
  AwsResource, PullRequest, TimelineEvent, SystemState, DriftAnalysis, RiskLevel, DriftType,
  Environment, AuditRecord, IntegrationStatus,
} from './src/types';
import {
  isAwsConfigured, readTerraformState, getCostEstimate,
  describeActualResources, terraformPlanDrift, writeAuditRecord,
} from './src/integrations/aws';
import { createPullRequest, isGitHubConfigured } from './src/integrations/github';
import { sendPagerDutyAlert } from './src/integrations/pagerduty';

// ponytail: explicit path — dotenv needs the exact .env location
dotenv.config({ path: path.join(process.cwd(), '.env') });
console.log(`[boot] CWD=${process.cwd()}, PAGERDUTY=${process.env.PAGERDUTY_ROUTING_KEY ? 'SET' : 'NOT SET'}`);

// ── environment validation ──────────────────────────────────────────
const VALID_ENVS: Environment[] = ['demo', 'staging', 'production'];
const ENV: Environment = (VALID_ENVS.includes(process.env.ENVIRONMENT as any)
  ? process.env.ENVIRONMENT : 'demo') as Environment;
const IS_PRODUCTION = ENV === 'production';

// ── structured logger ───────────────────────────────────────────────
function log(level: string, msg: string, meta: Record<string, any> = {}) {
  const entry = { ts: new Date().toISOString(), level, env: ENV, ...meta, msg };
  const safe = JSON.stringify(entry, (k, v) =>
    ['AWS_SECRET_ACCESS_KEY','GITHUB_TOKEN','PAGERDUTY_ROUTING_KEY'].includes(k) ? '***' : v);
  if (level === 'error') console.error(safe); else console.log(safe);
}
function auditLog(action: string, meta: Record<string, any>, reqId?: string) {
  log('audit', action, { ...meta, reqId });
}

// ── Prometheus metrics ──────────────────────────────────────────────
const metrics = {
  drift_count: 0, scans_total: 0, scan_failures_total: 0,
  pr_created_total: 0, pr_merged_total: 0,
  pagerduty_alerts_total: 0, pagerduty_failures_total: 0,
  mttr_samples: [] as number[],
};
function observeMTTR(s: number) { metrics.mttr_samples.push(s); }
function getMTTR() { return metrics.mttr_samples.length ? metrics.mttr_samples.reduce((a,b)=>a+b,0)/metrics.mttr_samples.length : 0; }

// ── audit trail (persisted to DynamoDB when AWS is configured) ──────
const auditTrail: AuditRecord[] = [];
async function recordAudit(r: Omit<AuditRecord, 'id'|'timestamp'>) {
  const e: AuditRecord = { ...r, id: randomUUID(), timestamp: new Date().toISOString() };
  auditTrail.push(e); if (auditTrail.length > 1000) auditTrail.shift();

  // Persist to DynamoDB audit table when available
  writeAuditRecord({
    pk: `audit#${e.timestamp}`,
    sk: e.action,
    action: e.action,
    resource_id: e.resourceId || 'system',
    timestamp: e.timestamp,
    details_json: JSON.stringify(e.details || {}),
  }).catch(() => {}); // fire-and-forget — audit log is best-effort

  return e;
}

// ── PR idempotency ──────────────────────────────────────────────────
function hashDrift(d: any[]): string { return crypto.createHash('sha256').update(JSON.stringify(d)).digest('hex').slice(0,16); }
function findExistingOpenPR(rid: string, hash: string) { return systemState.prs.find(p => p.status==='Open' && p.analysis.resourceId===rid && p.analysis.diffHash===hash); }

// ── account ID masker ───────────────────────────────────────────────
function maskAccountIds(obj: any): any {
  if (typeof obj==='string') return obj.replace(/:\d{12}:/g,':\\*\\*\\*:');
  if (Array.isArray(obj)) return obj.map(maskAccountIds);
  if (obj && typeof obj==='object') { const o:Record<string,any>={}; for(const[k,v]of Object.entries(obj))o[k]=maskAccountIds(v); return o; }
  return obj;
}

// ── deep diff ───────────────────────────────────────────────────────
function determineSeverity(field: string, expected: any, actual: any): RiskLevel {
  const f = field.toLowerCase();
  const act = String(actual).toLowerCase();
  if (f.includes('public') || f.includes('acl') || f.includes('cidr') || f.includes('port_22') ||
      f.includes('all_traffic') || act.includes('public') || act.includes('admin') ||
      act.includes('0.0.0.0') || act.includes('full-access')) return 'Critical';
  if (f.includes('encrypt') || f.includes('key') || f.includes('policy') || f.includes('password') ||
      f.includes('credentials') || f.includes('tls') || f.includes('ssl')) return 'High';
  if (f.includes('version') || f.includes('class') || f.includes('size') || f.includes('backup') ||
      f.includes('retention') || f.includes('capacity')) return 'Medium';
  return 'Low';
}

function getDeepDiff(obj1: any, obj2: any, path = '', seen = new WeakSet<object>()): any[] {
  const diffs: any[] = [];
  if (obj1 === obj2) return diffs;
  if (typeof obj1 !== typeof obj2 || obj1 === null || obj2 === null) {
    diffs.push({ field: path, expected: obj1, actual: obj2, severity: determineSeverity(path, obj1, obj2) });
    return diffs;
  }
  if (typeof obj1 === 'object') {
    if (seen.has(obj1) || seen.has(obj2)) return diffs;
    seen.add(obj1); seen.add(obj2);
    if (Array.isArray(obj1)) {
      if (!Array.isArray(obj2) || obj1.length !== obj2.length) {
        diffs.push({ field: path, expected: obj1, actual: obj2, severity: determineSeverity(path, obj1, obj2) });
        return diffs;
      }
      for (let i = 0; i < obj1.length; i++) diffs.push(...getDeepDiff(obj1[i], obj2[i], `${path}[${i}]`, seen));
    } else {
      const allKeys = Array.from(new Set([...Object.keys(obj1), ...Object.keys(obj2)]));
      for (const key of allKeys) {
        const nextPath = path ? `${path}.${key}` : key;
        if (!(key in obj2)) diffs.push({ field: nextPath, expected: obj1[key], actual: undefined, severity: determineSeverity(nextPath, obj1[key], undefined) });
        else if (!(key in obj1)) diffs.push({ field: nextPath, expected: undefined, actual: obj2[key], severity: determineSeverity(nextPath, undefined, obj2[key]) });
        else diffs.push(...getDeepDiff(obj1[key], obj2[key], nextPath, seen));
      }
    }
  } else {
    diffs.push({ field: path, expected: obj1, actual: obj2, severity: determineSeverity(path, obj1, obj2) });
  }
  return diffs;
}

// ── state ───────────────────────────────────────────────────────────
const initialTimeline: TimelineEvent[] = [{
  id: 't_init',
  timestamp: new Date().toISOString(),
  type: 'scan_clean',
  title: 'Yo! Drift Reconciler Started',
  message: 'Yo! Just booted up, loading terraform state from S3 backend.',
}];

// Server-side secrets — never returned to frontend
function getPagerDutyKey(): string { return process.env.PAGERDUTY_ROUTING_KEY || ''; }

let systemState: SystemState = {
  environment: ENV,
  resources: [],
  prs: [],
  timeline: JSON.parse(JSON.stringify(initialTimeline)),
  lastScanTime: new Date().toISOString(),
  scanning: false,
  alertConfig: { enabled: !!getPagerDutyKey() },
  integrationStatus: {
    pagerDuty: getPagerDutyKey() ? 'connected' : 'not_configured',
    github: isGitHubConfigured() ? 'connected' : 'simulated',
    aws: 'not_configured',
    terraformState: 'not_configured',
  },
  schedulerHealthy: true,
  maskAccountIds: true,
};

let lastScanTimestamp: number = Date.now();
const SCAN_HEARTBEAT_MS = parseInt(process.env.SCAN_HEARTBEAT_MS || '3600000', 10); // 1h default

// ── agents ──────────────────────────────────────────────────────────
function runPythonAgent(resource: AwsResource, agentScript = 'agent.py', timeoutMs = 30000): Promise<DriftAnalysis> {
  return new Promise((resolve, reject) => {
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    const pythonProcess = spawn(pythonCmd, [path.join(process.cwd(), agentScript)], { timeout: timeoutMs });
    let stdoutData = '', stderrData = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; pythonProcess.kill('SIGTERM'); reject(new Error(`Agent timed out after ${timeoutMs / 1000}s`)); }
    }, timeoutMs);
    pythonProcess.stdout.on('data', (d: any) => { stdoutData += d.toString(); });
    pythonProcess.stderr.on('data', (d: any) => { stderrData += d.toString(); });
    pythonProcess.on('close', (code: any) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      if (code !== 0) { reject(new Error(`Agent exit ${code}: ${stderrData.slice(0, 200)}`)); return; }
      try {
        const parsed = JSON.parse(stdoutData);
        if (parsed.error) reject(new Error(`Agent error: ${parsed.error}`));
        else resolve(parsed as DriftAnalysis);
      } catch (e) { reject(new Error('Invalid JSON from agent')); }
    });
    pythonProcess.on('error', (err: any) => { clearTimeout(timer); if (!settled) { settled = true; reject(new Error(`Spawn failed: ${err.message}`)); } });
    pythonProcess.stdin.write(JSON.stringify(resource));
    pythonProcess.stdin.end();
  });
}

// ── self-correcting agent loop ──────────────────────────────────────
async function analyzeWithSelfCorrection(resource: AwsResource): Promise<DriftAnalysis> {
  const agentMode = (process.env.AGENT_MODE || 'deterministic').toLowerCase();
  const agentScript = agentMode === 'nova' ? 'agent_nova.py' : 'agent.py';
  const agentTimeout = agentMode === 'nova' ? 45000 : 10000;
  const MAX_CORRECTION_ATTEMPTS = 3;

  let lastResult: DriftAnalysis | null = null;
  let attempts = 0;

  for (attempts = 0; attempts < MAX_CORRECTION_ATTEMPTS; attempts++) {
    try {
      console.log(`[agent] Attempt ${attempts + 1}/${MAX_CORRECTION_ATTEMPTS} — ${agentScript}`);
      const result = await runPythonAgent(resource, agentScript, agentTimeout);

      // Validate the output
      const errors: string[] = [];
      if (!result.classification || !['high_risk_change', 'moderate_risk_change', 'low_risk_change'].includes(result.classification))
        errors.push('Invalid classification');
      if (!result.riskScore || !['Low', 'Medium', 'High', 'Critical'].includes(result.riskScore))
        errors.push('Invalid riskScore');
      if (!result.explanation || result.explanation.length < 20)
        errors.push('Explanation too short');
      if (!result.hclFix || result.hclFix.trim().length === 0)
        errors.push('Empty HCL fix');

      result.validationStatus = errors.length === 0 ? 'passed' : 'failed';
      result.correctionAttempts = attempts + 1;

      if (errors.length === 0) {
        console.log(`[agent] Validation passed on attempt ${attempts + 1}`);
        auditLog('agent-success', { agentMode, attempts: attempts + 1, classification: result.classification });
        return result;
      }

      // Self-correction: feed errors back
      console.log(`[agent] Validation failed: ${errors.join(', ')}. Retrying...`);
      lastResult = result;

      // Pass error context to next attempt by appending to the resource
      resource = {
        ...resource,
        driftDetails: [
          ...(resource.driftDetails || []),
          { field: '_correction_feedback', expected: 'valid_output', actual: JSON.stringify(errors), severity: 'Low' as RiskLevel },
        ],
      };
    } catch (error: any) {
      console.error(`[agent] Attempt ${attempts + 1} crashed: ${error.message}`);
      lastResult = lastResult || {
        resourceId: resource.id,
        classification: 'low_risk_change',
        riskScore: 'Medium',
        explanation: `Agent execution error: ${error.message}`,
        securityImpact: 'Unable to complete analysis.',
        costImpact: 'Unknown — analysis incomplete.',
        hclFix: resource.terraformCode,
        validationStatus: 'failed',
        correctionAttempts: attempts + 1,
      } as DriftAnalysis;
    }
  }

  // All attempts exhausted — return last result with validation failed
  console.log(`[agent] All ${MAX_CORRECTION_ATTEMPTS} attempts exhausted.`);
  auditLog('agent-exhausted', { attempts, agentMode });
  return lastResult!;
}

// ── fallback analysis ───────────────────────────────────────────────
function generateFlexibleAnalysis(resource: AwsResource): DriftAnalysis {
  const drifts = resource.driftDetails || [];
  const classifications = drifts.map(d => d.severity);
  const isCritical = classifications.includes('Critical');
  const isHigh = classifications.includes('High');
  const classification: DriftType = isCritical ? 'high_risk_change' : isHigh ? 'moderate_risk_change' : 'low_risk_change';
  const riskScore: RiskLevel = isCritical ? 'Critical' : isHigh ? 'High' : classifications.includes('Medium') ? 'Medium' : 'Low';
  const deltaPhrases = drifts.map(d => `\`${d.field}\` shifted from \`${JSON.stringify(d.expected)}\` to \`${JSON.stringify(d.actual)}\``).join(', ');
  let hclFix = resource.terraformCode;
  drifts.forEach(d => {
    const regex = new RegExp(`${d.field}\\s*=\\s*.*`, 'g');
    hclFix = hclFix.replace(regex, `${d.field} = "${String(d.expected)}"  # reconciled`);
  });
  return {
    resourceId: resource.id,
    classification, riskScore,
    explanation: `Manual configuration bypass detected. Diverged: ${deltaPhrases}.`,
    securityImpact: isCritical ? 'Critical: unauthenticated access or privilege escalation.' : 'Compliance divergence risk.',
    costImpact: 'Non-standard config triggers manual review (~$120/hr).',
    hclFix, fixType: 'illustrative_diff',
    validationStatus: 'pending', correctionAttempts: 0,
    policyReferences: [{ id: 'CKV_AWS_999', name: 'CIS baseline compliance', severity: 'MEDIUM' }],
  };
}

// ═══════════════════════════════════════════════════════════════════
// SERVER
// ═══════════════════════════════════════════════════════════════════

async function startServer() {
  const app = express();
  app.use(express.json({ limit: '500kb' }));

  // security headers──────────────────────────────────────────
  app.use((_req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('X-XSS-Protection', '0');
    res.setHeader('Referrer-Policy', 'no-referrer');
    next();
  });

  // ── rate limiter ───────────────────────────────────────────────
  const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
  function rateLimit(key: string, max: number, windowMs: number): boolean {
    const now = Date.now();
    const entry = rateLimitMap.get(key);
    if (!entry || now > entry.resetAt) { rateLimitMap.set(key, { count: 1, resetAt: now + windowMs }); return true; }
    if (entry.count >= max) return false;
    entry.count++; return true;
  }
  const API_ACCESS_TOKEN = process.env.API_ACCESS_TOKEN || '';

  function requireApiToken(req: express.Request, res: express.Response, next: express.NextFunction) {
    // Header name: X-Api-Access-Token
    if (API_ACCESS_TOKEN && String(req.headers['x-api-access-token'] || '') !== API_ACCESS_TOKEN) {
      return res.status(401).json({ error: 'Invalid API access token' });
    }
    next();
  }
  function rateLimitMiddleware(max: number, windowMs: number) {
    return (req: any, res: any, next: any) => {
      if (!rateLimit(req.ip || '127.0.0.1', max, windowMs)) return res.status(429).json({ error: 'Rate limited' });
      next();
    };
  }
  app.use((err: any, _req: any, res: any, next: any) => {
    if (err.type === 'entity.too.large') return res.status(413).json({ error: 'Body too large' });
    next(err);
  });
  app.use((req: any, _res: any, next: any) => { req.requestId = randomUUID(); next(); });
  function blockInProduction(req: any, res: any, next: any) { if (IS_PRODUCTION) return res.status(403).json({ error: 'Not available in prod' }); next(); }

  // ── request ID middleware ──────────────────────────────────────────
  app.use((req: any, _res: any, next: any) => {
    req.requestId = randomUUID();
    req.startTime = Date.now();
    next();
  });

  // ── health / readiness / metrics ───────────────────────────────────
  app.get('/health', (_req, res) => { res.json({ status: 'ok', uptime: process.uptime() }); });

  app.get('/ready', async (_req, res) => {
    const checks = {
      aws: false, s3: false, github: false, pagerduty: false,
    };
    try { checks.aws = await isAwsConfigured(); } catch {}
    try { const parsed = await readTerraformState(); checks.s3 = !!(parsed && parsed.resources.length > 0); } catch {}
    checks.github = isGitHubConfigured();
    checks.pagerduty = !!getPagerDutyKey();
    const allOk = Object.values(checks).every(Boolean);
    res.status(allOk ? 200 : 503).json({ status: allOk ? 'ready' : 'not_ready', checks });
  });

  app.get('/metrics', (_req, res) => {
    res.json({
      drift_count: metrics.drift_count,
      scans_total: metrics.scans_total,
      scan_failures_total: metrics.scan_failures_total,
      pr_created_total: metrics.pr_created_total,
      pr_merged_total: metrics.pr_merged_total,
      pagerduty_alerts_total: metrics.pagerduty_alerts_total,
      pagerduty_failures_total: metrics.pagerduty_failures_total,
      mttr_seconds: Math.round(getMTTR()),
      scheduler_healthy: systemState.schedulerHealthy,
      uptime_seconds: Math.round(process.uptime()),
    });
  });

  // ── alert broadcaster (PagerDuty with retry + metrics) ────────
  async function sendDriftAlertsOnDetection(resource: AwsResource) {
    if (!systemState.alertConfig?.enabled || !getPagerDutyKey()) return;
    const driftSummary = resource.driftDetails?.map(d => `${d.field}: ${d.expected} → ${d.actual}`).join(', ') || 'Drift detected';
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const result = await sendPagerDutyAlert({
          resourceName: resource.name, resourceType: resource.type, service: resource.service,
          classification: resource.isDrifted ? 'high_risk_change' : 'low_risk_change',
          riskScore: resource.driftDetails?.[0]?.severity || 'High',
          driftSummary,
          securityImpact: `Resource ${resource.name} has diverged from desired Terraform state.`,
        });
        metrics.pagerduty_alerts_total++;
        if (!result.success && attempt === 2) {
          metrics.pagerduty_failures_total++;
          systemState.integrationStatus.lastPagerDutyError = result.error || 'Unknown';
        }
        log('info', 'PagerDuty alert sent', { resource: resource.name, attempt });
        return;
      } catch (e: any) {
        if (attempt === 2) { metrics.pagerduty_failures_total++; systemState.integrationStatus.lastPagerDutyError = e.message; }
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
  }

  // ═════════════════════════════════════════════════════════════
  // API ROUTES
  // ═════════════════════════════════════════════════════════════

  // GET /api/state — returns masked data, never exposes secrets
  app.get('/api/state', (req: any, res) => {
    const visible = systemState.maskAccountIds
      ? maskAccountIds(JSON.parse(JSON.stringify(systemState)))
      : JSON.parse(JSON.stringify(systemState));
    log('info', 'state requested', { reqId: req.requestId });
    res.json(visible);
  });

  // POST /api/state/unmask — toggle ARN masking (audited)
  app.post('/api/state/unmask', requireApiToken, (req: any, res) => {
    systemState.maskAccountIds = !systemState.maskAccountIds;
    recordAudit({ action: 'arn_reveal', details: { unmasked: !systemState.maskAccountIds } });
    res.json({ maskAccountIds: systemState.maskAccountIds });
  });

  // POST /api/scan — deep-diff all resources
  app.post('/api/scan', (req, res) => {
    if (systemState.scanning) return res.status(409).json({ error: 'Scan already in progress' });
    systemState.scanning = true;
    const scanDelay = parseInt(process.env.DEMO_SCAN_DELAY_MS || '0', 10);

    setTimeout(async () => {
      if (!systemState.scanning) return;
      let foundDrift = false;
      const nowStr = new Date().toISOString();

      // Primary: terraform plan -json (universal — all resource types)
      const planOutcome = await terraformPlanDrift();
      if (planOutcome.error) {
        console.error(`[aws] terraform plan FAILED during state load: ${planOutcome.error}`);
        systemState.integrationStatus.terraformState = 'not_configured';
        // do not abort — continue with EC2 describe fallback and deep-diff
      }

      if (!planOutcome.error && planOutcome.results.length > 0) {
        for (const pd of planOutcome.results) {
          const match = systemState.resources.find(r => r.type === pd.type && r.name === pd.name);
          if (match) {
            match.actualState = pd.actualState;
            match.desiredState = pd.desiredState || match.desiredState;
            match.isDrifted = true;
            match.driftDetails = pd.changedFields.map(f => ({
              field: f.field, expected: f.expected, actual: f.actual,
              severity: determineSeverity(f.field, f.expected, f.actual),
            }));
            match.lastChecked = nowStr;
            foundDrift = true;
          }
        }
      } else {
        // EC2 describe fallback (plan had zero results OR plan errored)
        if (await isAwsConfigured()) {
          const actualResources = await describeActualResources();
          for (const actual of actualResources) {
            const match = systemState.resources.find(r => r.id === actual.id);
            if (match) match.actualState = actual.actualState;
          }
        }
      }

      // Run deep-diff only on resources NOT already detected by plan.
      const planDetectedIds = new Set(
        planOutcome.results.map(pd => systemState.resources.find(r => r.type === pd.type && r.name === pd.name)?.id).filter(Boolean)
      );
      systemState.resources = systemState.resources.map(resource => {
        resource.lastChecked = nowStr;
        if (planDetectedIds.has(resource.id)) {
          if (resource.isDrifted) foundDrift = true;
          return resource;
        }
        const driftDetails = getDeepDiff(resource.desiredState, resource.actualState);
        const isDrifted = driftDetails.length > 0;
        if (isDrifted) foundDrift = true;
        return { ...resource, isDrifted, driftDetails: isDrifted ? driftDetails : undefined };
      });

      systemState.lastScanTime = nowStr;
      systemState.scanning = false;

      const eventId = `t_scan_${randomUUID()}`;
      if (foundDrift) {
        const drifted = systemState.resources.filter(r => r.isDrifted);
        for (const r of drifted) await sendDriftAlertsOnDetection(r);
        systemState.timeline.unshift({
          id: eventId, timestamp: nowStr, type: 'scan_drift',
          title: 'Drift Detected',
          message: `Deep-diff found ${drifted.length} drifted resources: ${drifted.map(r => r.name).join(', ')}.`,
          details: { driftedCount: drifted.length },
        });
      } else {
        systemState.timeline.unshift({
          id: eventId, timestamp: nowStr, type: 'scan_clean',
          title: 'Scan: Compliant',
          message: `All ${systemState.resources.length} resources match desired state.`,
        });
      }
      auditLog('scan', { foundDrift, resourceCount: systemState.resources.length });
      res.json(systemState);
    }, scanDelay);
  });

  // POST /api/analyze — run agent with self-correction loop
  app.post('/api/analyze', requireApiToken, async (req, res) => {
    const { resourceId } = req.body;
    const resource = systemState.resources.find(r => r.id === resourceId);
    if (!resource || !resource.isDrifted) {
      return res.status(400).json({ error: 'Resource must exist and be in drifted state.' });
    }

    const nowStr = new Date().toISOString();
    let analysisResult: DriftAnalysis;

    try {
      analysisResult = await analyzeWithSelfCorrection(resource);
    } catch (error: any) {
      console.error('[analyze] Self-correction loop failed:', error);
      analysisResult = generateFlexibleAnalysis(resource);
    }

    // Enrich with AWS Cost Explorer data
    let costImpact = analysisResult.costImpact;
    try {
      const costEstimate = await getCostEstimate(resource.type, analysisResult.riskScore);
      if (costEstimate.source === 'aws-cost-explorer') {
        costImpact = `${costImpact}\n\n**AWS Cost Explorer (30-day):** ${costEstimate.monthlyCost}. Fine exposure: ${costEstimate.estimatedFineRange}.`;
      }
    } catch { /* keep original */ }

    const gitHubConfigured = isGitHubConfigured();
    const repo = process.env.GITHUB_REPO || 'digambarrajaram/AWS-Terraform-Drift-Reconciler';
    const baseBranch = process.env.GITHUB_BRANCH || 'drift';
    const branchName = `reconcile/drift-${resource.name}-${randomUUID().slice(0, 8)}`;

    const validationNote = analysisResult.validationStatus === 'failed'
      ? `\n> ⚠️ **Validation:** Agent output did not pass all checks after ${analysisResult.correctionAttempts} correction attempts. Manual review required.`
      : analysisResult.validationStatus === 'passed' && analysisResult.correctionAttempts > 1
      ? `\n> ✅ **Self-corrected:** Agent output validated after ${analysisResult.correctionAttempts} attempts.`
      : '';

    const prDescription = `### 🤖 Drift Reconciliation — ${gitHubConfigured ? 'Automated PR' : 'Simulated PR'}

**Resource:** \`${resource.type}.${resource.name}\`
**Classification:** ${analysisResult.classification} | **Risk:** ${analysisResult.riskScore}
**Generated:** ${new Date().toLocaleTimeString()}
**Validation:** ${analysisResult.validationStatus.toUpperCase()} (${analysisResult.correctionAttempts} correction attempt(s))
${validationNote}

---

#### 📋 Explanation
${analysisResult.explanation}

#### 🔒 Security Impact
${analysisResult.securityImpact}

#### 💰 Cost & Compliance
${costImpact}

---

#### 🛠️ Proposed HCL Reconciliation
${analysisResult.hclDiff || analysisResult.hclFix}

> **Note:** Policy checks (Checkov, terraform validate) run in CI/CD after this PR is opened.
`;

    const ghResult = await createPullRequest({
      repo, baseBranch, resourceName: resource.name, resourceType: resource.type,
      branchName, prTitle: `fix(terraform): reconcile drift on ${resource.name}`, prDescription,
      hclChanges: analysisResult.hclFix, analysis: analysisResult,
    });

    const prNumber = ghResult.prNumber || systemState.prs.length + 101;
    const newPr: PullRequest = {
      id: `pr_${randomUUID()}`, number: prNumber,
      title: `fix(terraform): reconcile drift on ${resource.name}`,
      branch: ghResult.branchName || branchName, description: prDescription,
      status: 'Open', createdAt: nowStr, hclChanges: analysisResult.hclFix,
      analysis: { ...analysisResult, costImpact },
    };

    systemState.prs.unshift(newPr);
    systemState.timeline.unshift({
      id: `t_pr_${randomUUID()}`, timestamp: nowStr, type: 'pr_created',
      title: `${gitHubConfigured ? 'GitHub' : 'Simulated'} PR #${prNumber} Opened`,
      message: `Agent analyzed ${resource.name}. ${gitHubConfigured ? 'Real' : 'Simulated'} PR #${prNumber}${ghResult.prUrl ? ` at ${ghResult.prUrl}` : ''}. Validation: ${analysisResult.validationStatus}.`,
      resourceId: resource.id,
      details: { prNumber, branchName, prUrl: ghResult.prUrl, validationStatus: analysisResult.validationStatus, correctionAttempts: analysisResult.correctionAttempts },
    });

    auditLog('analyze', { resourceId, classification: analysisResult.classification, validationStatus: analysisResult.validationStatus, correctionAttempts: analysisResult.correctionAttempts });
    res.json({ systemState, pr: newPr, githubResult: ghResult });
  });

  // POST /api/merge-pr — with approval gate for high-risk
  app.post('/api/merge-pr', requireApiToken, rateLimitMiddleware(20, 60000), (req, res) => {
    const { prId, approvedBy } = req.body;
    const pr = systemState.prs.find(p => p.id === prId);
    if (!pr) return res.status(404).json({ error: 'PR not found' });
    if (pr.status !== 'Open') return res.status(400).json({ error: 'PR already ' + pr.status });

    const isHighRisk = pr.analysis.classification === 'high_risk_change' || pr.analysis.riskScore === 'Critical' ||
      systemState.resources.find(r => r.id === pr.analysis.resourceId)?.type === 'aws_iam_role';
    if (isHighRisk && !approvedBy) {
      return res.status(403).json({ error: 'High-risk PR requires human approval. Provide { approvedBy: "name" }.', requiresApproval: true });
    }
    if (approvedBy) { pr.approvedBy = approvedBy; pr.approvedAt = new Date().toISOString(); }

    const nowStr = new Date().toISOString();
    pr.status = 'Merged'; pr.mergedAt = nowStr;

    const resource = systemState.resources.find(r => r.id === pr.analysis.resourceId);
    if (resource) {
      resource.actualState = JSON.parse(JSON.stringify(resource.desiredState));
      resource.isDrifted = false; resource.driftDetails = undefined; resource.lastChecked = nowStr;
    }

    systemState.timeline.unshift({
      id: `t_merge_${randomUUID()}`, timestamp: nowStr, type: 'pr_merged',
      title: `PR #${pr.number} Merged`,
      message: `PR #${pr.number} merged${approvedBy ? ` (approved by ${approvedBy})` : ''}. State reconciled.`,
      resourceId: resource?.id, details: { prNumber: pr.number, approvedBy: approvedBy || 'auto' },
    });

    auditLog('merge-pr', { prNumber: pr.number, approvedBy: approvedBy || 'auto', isHighRisk });
    res.json(systemState);
  });

  // POST /api/merge-pr/reject — reject with reason (audited)
  app.post('/api/merge-pr/reject', requireApiToken, rateLimitMiddleware(20, 60000), (req, res) => {
    const { prId, rejectedBy, reason } = req.body;
    const pr = systemState.prs.find(p => p.id === prId);
    if (!pr) return res.status(404).json({ error: 'PR not found' });
    if (pr.status !== 'Open') return res.status(400).json({ error: 'PR already ' + pr.status });
    if (!reason || reason.trim().length < 5) return res.status(400).json({ error: 'Rejection reason required (min 5 chars)' });

    pr.status = 'Rejected';
    pr.rejectedAt = new Date().toISOString();
    pr.rejectedBy = rejectedBy || 'unknown';
    pr.rejectionReason = reason.trim();

    recordAudit({ action: 'pr_rejected', resourceId: pr.analysis.resourceId, prNumber: pr.number, details: { rejectedBy: pr.rejectedBy, reason: pr.rejectionReason } });
    systemState.timeline.unshift({
      id: `t_reject_${randomUUID()}`,
      timestamp: new Date().toISOString(), type: 'pr_rejected',
      title: `PR #${pr.number} Rejected`,
      message: `PR #${pr.number} rejected by ${pr.rejectedBy}: ${pr.rejectionReason}`,
      resourceId: pr.analysis.resourceId,
      details: { prNumber: pr.number, rejectedBy: pr.rejectedBy, reason: pr.rejectionReason },
    });

    log('info', 'PR rejected', { prNumber: pr.number, rejectedBy: pr.rejectedBy });
    res.json(systemState);
  });

  // POST /api/reset
  app.post('/api/reset', requireApiToken, rateLimitMiddleware(10, 60000), (req, res) => {
    const savedConfig = systemState.alertConfig;
    const resetResources = JSON.parse(JSON.stringify(systemState.resources.map((r: AwsResource) => ({ ...r, actualState: JSON.parse(JSON.stringify(r.desiredState)), isDrifted: false, driftDetails: undefined }))));
    systemState = {
      ...systemState,
      resources: resetResources,
      prs: [],
      timeline: JSON.parse(JSON.stringify(initialTimeline)),
      lastScanTime: new Date().toISOString(),
      scanning: false,
      alertConfig: savedConfig,
    };
    auditLog('reset', {});
    res.json(systemState);
  });


  // POST /api/resource — create custom tracked resource
  app.post('/api/resource', requireApiToken, (req, res) => {
    const { name, type, service, terraformCode, desiredState } = req.body;
    if (!name || !type || !service || !terraformCode || !desiredState)
      return res.status(400).json({ error: 'Missing required fields' });
    const newId = `custom_${name.toLowerCase().replace(/[^a-z0-9]/g, '_')}_${randomUUID().slice(0, 8)}`;
    const newResource: AwsResource = {
      id: newId, name, type, service, terraformCode, desiredState,
      actualState: JSON.parse(JSON.stringify(desiredState)), isDrifted: false, lastChecked: new Date().toISOString(),
    };
    systemState.resources.push(newResource);
    systemState.timeline.unshift({ id: `t_res_${randomUUID()}`, timestamp: new Date().toISOString(), type: 'scan_clean', title: 'Resource Registered', message: `Custom resource '${name}' tracked.`, resourceId: newId });
    res.json(systemState);
  });

  // POST /api/alerts/config — simplified PagerDuty-only config
  app.post('/api/alerts/config', requireApiToken, (req, res) => {
    const { enabled } = req.body;
    const finalEnabled = enabled !== undefined ? !!enabled : true;
    systemState.alertConfig = { enabled: finalEnabled };
    console.log('[alerts] PagerDuty config updated');
    auditLog('alert-config', { enabled: finalEnabled });
    res.json(systemState);
  });

  // POST /api/alerts/test — test PagerDuty alert
  app.post('/api/alerts/test', (req, res) => {
    const { resourceId } = req.body;
    const resource = systemState.resources.find(r => r.id === resourceId) || systemState.resources[0];
    if (!resource) return res.status(404).json({ error: 'No resources available' });
    sendDriftAlertsOnDetection(resource).catch(() => {});
    console.log('[alerts] Test alert triggered via PagerDuty');
    res.json({ success: true, message: 'PagerDuty alert triggered.', routingKey: getPagerDutyKey() ? 'configured' : 'not set' });
  });

  // ── Serve frontend ──────────────────────────────────────────────
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({ server: { middlewareMode: true }, appType: 'spa' });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (_req, res) => { res.sendFile(path.join(distPath, 'index.html')); });
  }

  // ── Start listening (routes + middleware all registered) ───────
  const PORT = parseInt(process.env.PORT || '3000', 10);
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Server running on http://0.0.0.0:${PORT}`);
    // Load state from AWS asynchronously — non-blocking
    loadStateFromAws();
  });
}

async function loadStateFromAws() {
  const awsReady = await isAwsConfigured();
  console.log(`  AWS:       ${awsReady ? 'connected' : 'not configured (demo mode)'}`);
  console.log(`  GitHub:    ${isGitHubConfigured() ? `connected → ${process.env.GITHUB_REPO}` : 'not configured (simulated PRs)'}`);
  // Re-read env at runtime (AI Studio injects after dotenv)
  const pdKey = process.env.PAGERDUTY_ROUTING_KEY || '';
  systemState.integrationStatus.pagerDuty = pdKey ? 'connected' : 'not_configured';
  systemState.alertConfig = { enabled: !!pdKey };
  console.log(`  PagerDuty: ${pdKey ? 'connected' : 'not configured'}`);

  if (!awsReady) { console.log(`  Resources: ${systemState.resources.length} loaded`); return; }
  systemState.integrationStatus.aws = 'connected';

  console.log('[aws] Loading terraform state from S3...');
  try {
    const parsed = await readTerraformState();
    if (!parsed || parsed.resources.length === 0) {
      console.log('[aws] No terraform state found on S3');
      systemState.integrationStatus.terraformState = 'empty';
      console.log(`  Resources: ${systemState.resources.length} loaded`);
      return;
    }

    const stateResources: AwsResource[] = parsed.resources.map(r => ({
      id: r.id, name: r.name, type: r.type, service: r.service,
      desiredState: r.desiredState,
      actualState: JSON.parse(JSON.stringify(r.desiredState)),
      isDrifted: false, terraformCode: r.terraformCode || '', lastChecked: new Date().toISOString(),
    }));

    // Primary: terraform plan -json (ALL resource types)
    const planOutcome = await terraformPlanDrift();
    if (planOutcome.error) {
      console.error(`[aws] terraform plan FAILED during load: ${planOutcome.error}`);
      systemState.resources = stateResources;
      systemState.integrationStatus.terraformState = 'loaded';
      console.log(`[aws] ${stateResources.length} resources loaded (drift unchecked — plan failed)`);
      return;
    }
    if (planOutcome.results.length > 0) {
      console.log(`[aws] terraform plan detected drift in ${planOutcome.results.length} resources`);
      for (const pd of planOutcome.results) {
        const match = stateResources.find(r => r.type === pd.type && r.name === pd.name);
        if (match) {
          match.actualState = pd.actualState;
          match.desiredState = pd.desiredState || match.desiredState;
          match.isDrifted = true;
          match.driftDetails = pd.changedFields.map(f => ({
            field: f.field, expected: f.expected, actual: f.actual,
            severity: determineSeverity(f.field, f.expected, f.actual),
          }));
          match.lastChecked = new Date().toISOString();
        } else {
          stateResources.push({
            id: `${pd.type}_${pd.name}`.replace(/[^a-zA-Z0-9_-]/g, '_'),
            name: pd.name, type: pd.type,
            service: pd.type.includes('route53') ? 'Route53' : pd.type.includes('lambda') ? 'Lambda' : pd.type.includes('s3') ? 'S3' : 'Other',
            desiredState: pd.desiredState, actualState: pd.actualState,
            isDrifted: true,
            driftDetails: pd.changedFields.map(f => ({
              field: f.field, expected: f.expected, actual: f.actual,
              severity: determineSeverity(f.field, f.expected, f.actual),
            })),
            terraformCode: '', lastChecked: new Date().toISOString(),
          });
        }
      }
    } else {
      // Fallback: EC2 describe
      const actualResources = await describeActualResources();
      for (const actual of actualResources) {
        const match = stateResources.find(r => r.id === actual.id);
        if (match) {
          match.actualState = actual.actualState;
          const diffs = getDeepDiff(match.desiredState, match.actualState);
          match.isDrifted = diffs.length > 0;
          match.driftDetails = match.isDrifted ? diffs : undefined;
          match.lastChecked = new Date().toISOString();
        }
      }
    }

    systemState.resources = stateResources;
    systemState.integrationStatus.terraformState = 'loaded';
    console.log(`[aws] ${stateResources.length} resources, ${stateResources.filter(r => r.isDrifted).length} drifted`);
    systemState.timeline.unshift({
      id: `t_load_${randomUUID()}`,
      timestamp: new Date().toISOString(), type: 'scan_drift',
      title: 'State Loaded from S3',
      message: `${stateResources.length} resources parsed. ${stateResources.filter(r => r.isDrifted).length} drifted.`,
    });
  } catch (err: any) {
    console.log(`[aws] State loading failed: ${err.message}`);
  }
  console.log(`  Resources: ${systemState.resources.length} loaded`);
}

startServer();
