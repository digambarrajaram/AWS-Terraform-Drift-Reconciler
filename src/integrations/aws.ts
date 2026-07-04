/**
 * AWS Integration Layer
 * - S3: Terraform state storage (read/write tfstate files)
 * - EC2/other services: describe actual resource state for drift comparison
 * - Cost Explorer: real pricing data
 * - STS: account ID lookup
 *
 * No separate state-persistence DynamoDB table. State lives in:
 *   s3://{project}-state-{account_id}/production/terraform.tfstate
 * State locking is handled by the existing bootstrap DynamoDB table:
 *   {project}-locks-{account_id}
 */

import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import {
  CostExplorerClient,
  GetCostAndUsageCommand,
} from "@aws-sdk/client-cost-explorer";
import {
  DynamoDBClient,
  PutItemCommand,
  QueryCommand,
} from "@aws-sdk/client-dynamodb";
import {
  EC2Client,
  DescribeSecurityGroupsCommand,
  DescribeInstancesCommand,
} from "@aws-sdk/client-ec2";
import { STSClient, GetCallerIdentityCommand } from "@aws-sdk/client-sts";

// ── Lazy client init ────────────────────────────────────────────────

let _s3: S3Client | null = null;
let _ce: CostExplorerClient | null = null;
let _ec2: EC2Client | null = null;
let _accountId: string | null = null;

function getS3(): S3Client | null {
  if (!process.env.AWS_ACCESS_KEY_ID) return null;
  if (!_s3) _s3 = new S3Client({ region: process.env.AWS_REGION || "us-east-1" });
  return _s3;
}

function getCostExplorer(): CostExplorerClient | null {
  if (!process.env.AWS_ACCESS_KEY_ID) return null;
  if (!_ce) _ce = new CostExplorerClient({ region: process.env.AWS_REGION || "us-east-1" });
  return _ce;
}

function getEC2(): EC2Client | null {
  if (!process.env.AWS_ACCESS_KEY_ID) return null;
  if (!_ec2) _ec2 = new EC2Client({ region: process.env.AWS_REGION || "us-east-1" });
  return _ec2;
}

export async function getAccountId(): Promise<string | null> {
  if (_accountId) return _accountId;
  if (!process.env.AWS_ACCESS_KEY_ID) return null;
  try {
    const sts = new STSClient({ region: process.env.AWS_REGION || "us-east-1" });
    const resp = await sts.send(new GetCallerIdentityCommand({}));
    _accountId = resp.Account || null;
    sts.destroy();
    return _accountId;
  } catch {
    return null;
  }
}

export async function isAwsConfigured(): Promise<boolean> {
  return (await getAccountId()) !== null;
}

// ── DynamoDB: Audit Trail Persistence ───────────────────────────────

let _ddb: DynamoDBClient | null = null;

function getDynamoDB(): DynamoDBClient | null {
  if (!process.env.AWS_ACCESS_KEY_ID) return null;
  if (!_ddb) _ddb = new DynamoDBClient({ region: process.env.AWS_REGION || "us-east-1" });
  return _ddb;
}

function getAuditTableName(): string {
  const prefix = process.env.TF_RESOURCE_PREFIX || process.env.TF_PROJECT_NAME || "aws-terraform-drift-reconciler";
  const env = process.env.ENVIRONMENT || "dev";
  return `${prefix}-${env}-drift-audit`;
}

export async function writeAuditRecord(record: {
  pk: string;          // e.g. "audit#<timestamp>"
  sk: string;          // e.g. "scan" | "pr_created" | "pr_merged"
  action: string;
  resource_id: string; // for GSI
  timestamp: string;
  details_json: string;
}): Promise<boolean> {
  const ddb = getDynamoDB();
  if (!ddb) return false;

  try {
    await ddb.send(new PutItemCommand({
      TableName: getAuditTableName(),
      Item: {
        pk: { S: record.pk },
        sk: { S: record.sk },
        action: { S: record.action },
        resource_id: { S: record.resource_id },
        timestamp: { S: record.timestamp },
        details_json: { S: record.details_json },
      },
    }));
    return true;
  } catch (err: any) {
    console.log(`[aws] DynamoDB audit write failed: ${err.message}`);
    return false;
  }
}

export async function queryAuditTrail(action?: string, resourceId?: string, limit = 50): Promise<any[]> {
  const ddb = getDynamoDB();
  if (!ddb) return [];

  try {
    if (resourceId) {
      const resp = await ddb.send(new QueryCommand({
        TableName: getAuditTableName(),
        IndexName: "resource-index",
        KeyConditionExpression: "resource_id = :rid",
        ExpressionAttributeValues: { ":rid": { S: resourceId } },
        Limit: limit,
        ScanIndexForward: false,
      }));
      return (resp.Items || []).map(parseAuditItem);
    }
    if (action) {
      const resp = await ddb.send(new QueryCommand({
        TableName: getAuditTableName(),
        IndexName: "action-index",
        KeyConditionExpression: "#a = :action",
        ExpressionAttributeNames: { "#a": "action" },
        ExpressionAttributeValues: { ":action": { S: action } },
        Limit: limit,
        ScanIndexForward: false,
      }));
      return (resp.Items || []).map(parseAuditItem);
    }
    return [];
  } catch (err: any) {
    console.log(`[aws] DynamoDB audit query failed: ${err.message}`);
    return [];
  }
}

function parseAuditItem(item: Record<string, any>): any {
  try {
    return {
      pk: item.pk?.S,
      sk: item.sk?.S,
      action: item.action?.S,
      resource_id: item.resource_id?.S,
      timestamp: item.timestamp?.S,
      details: JSON.parse(item.details_json?.S || '{}'),
    };
  } catch {
    return item;
  }
}

function getStateBucket(): string {
  return process.env.TF_STATE_BUCKET || "aws-terraform-drift-reconciler-state";
}
function getStateKey(): string {
  return process.env.TF_STATE_KEY || "ec2/terraform.tfstate";
}

// ── S3: Terraform State ─────────────────────────────────────────────

export interface ParsedTerraformState {
  resources: {
    id: string;
    name: string;
    type: string;
    service: string;
    terraformCode: string;
    desiredState: Record<string, any>;
  }[];
  rawState: Record<string, any>;
}

export async function readTerraformState(): Promise<ParsedTerraformState | null> {
  const s3 = getS3();
  if (!s3) return null;
  const bucket = getStateBucket();
  const key = getStateKey();

  try {
    const resp = await s3.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
    const body = await resp.Body?.transformToString();
    if (!body) return null;
    const raw = JSON.parse(body);

    // Parse resources from terraform state format
    const resources: ParsedTerraformState["resources"] = [];
    const tfResources = raw?.resources || [];

    for (const r of tfResources) {
      if (r.mode !== "managed") continue;
      const type = r.type || "unknown";
      const name = r.name || r.instances?.[0]?.attributes?.id || "unnamed";

      // ponytail: terraform state JSON contains resource attributes, not HCL source.
      // HCL is loaded from local .tf files (see loadTerraformFilesForResources below).
      resources.push({
        id: `${type}_${name}`.replace(/[^a-zA-Z0-9_-]/g, "_"),
        name,
        type,
        service: mapTerraformTypeToService(type),
        terraformCode: "",
        desiredState: r.instances?.[0]?.attributes || {},
      });
    }

    // Enrich with HCL source from local terraform files
    await enrichWithTerraformCode(resources);

    return { resources, rawState: raw };
  } catch (err: any) {
    console.log(`[aws] Could not read state from s3://${bucket}/${key}: ${err.message}`);
    return null;
  }
}

// ── HCL loading from local terraform files ──────────────────────────
// ponytail: terraform state JSON doesn't contain HCL source.
// We read .tf files from the local terraform directory to associate
// resource blocks with their source code dynamically.

async function loadTerraformFiles(dir: string): Promise<Record<string, string>> {
  const tfFiles: Record<string, string> = {};
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== '.terraform' && entry.name !== 'modules') {
        Object.assign(tfFiles, await loadTerraformFiles(fullPath));
      } else if (entry.isFile() && entry.name.endsWith('.tf')) {
        tfFiles[fullPath] = fs.readFileSync(fullPath, 'utf-8');
      }
    }
  } catch { /* directory may not exist */ }
  return tfFiles;
}

function extractResourceBlocks(tfContent: string): Record<string, string> {
  // Extract resource "type" "name" { ... } blocks and map to type_name keys
  const blocks: Record<string, string> = {};
  const regex = /resource\s+"([^"]+)"\s+"([^"]+)"\s*\{/g;
  let match;
  while ((match = regex.exec(tfContent)) !== null) {
    const type = match[1];
    const name = match[2];
    // Find the matching closing brace
    let depth = 1;
    let end = match.index + match[0].length;
    for (let i = end; i < tfContent.length && depth > 0; i++) {
      if (tfContent[i] === '{') depth++;
      else if (tfContent[i] === '}') depth--;
      if (depth === 0) end = i + 1;
    }
    blocks[`${type}_${name}`] = tfContent.slice(match.index, end);
  }
  return blocks;
}

async function enrichWithTerraformCode(resources: ParsedTerraformState["resources"]): Promise<void> {
  const tfDir = process.env.TERRAFORM_DIR || path.join(process.cwd(), 'terraform');
  const tfFiles = await loadTerraformFiles(tfDir);

  // Build a map of all resource blocks from all .tf files
  const allBlocks: Record<string, string> = {};
  for (const [filePath, content] of Object.entries(tfFiles)) {
    const blocks = extractResourceBlocks(content);
    Object.assign(allBlocks, blocks);
  }

  // Match resources to their HCL source
  for (const resource of resources) {
    const key = `${resource.type}_${resource.name}`;
    if (allBlocks[key]) {
      resource.terraformCode = allBlocks[key];
    } else if (Object.keys(allBlocks).length > 0) {
      // Best-effort: use the first matching type
      const typeBlocks = Object.entries(allBlocks).filter(([k]) => k.startsWith(resource.type + '_'));
      if (typeBlocks.length > 0) {
        resource.terraformCode = typeBlocks[0][1];
      }
    }
  }

  console.log(`[aws] Loaded ${Object.keys(allBlocks).length} HCL resource blocks from ${Object.keys(tfFiles).length} .tf files`);
}


function mapTerraformTypeToService(type: string): string {
  if (type.includes("s3") || type.includes("bucket")) return "S3";
  if (type.includes("security_group") || type.includes("vpc") || type.includes("subnet")) return "VPC";
  if (type.includes("iam") || type.includes("role") || type.includes("policy")) return "IAM";
  if (type.includes("rds") || type.includes("db_instance")) return "RDS";
  if (type.includes("instance") || type.includes("ec2")) return "EC2";
  if (type.includes("elasticache") || type.includes("redis")) return "ElastiCache";
  if (type.includes("lb") || type.includes("load_balancer")) return "ALB";
  if (type.includes("nat")) return "VPC";
  return "Other";
}

// ── Terraform Plan — universal drift detection for ALL resource types ─
// ponytail: terraform plan -json diffs desired vs actual for every resource
// type (Route53, S3, RDS, IAM, Lambda, EC2, etc.) in one command. No need
// for per-service describe calls. Falls back to direct AWS API calls when
// terraform binary isn't available.

import { execFile, execFileSync, execSync } from 'child_process';

// Resolve terraform binary path — not hard-coded to /usr/bin
function findTerraform(): string {
  if (process.env.TERRAFORM_PATH) return process.env.TERRAFORM_PATH;
  try {
    // which/where works on Linux, macOS, and Windows (where.exe)
    const isWindows = process.platform === 'win32';
    const cmd = isWindows ? 'where terraform' : 'which terraform';
    const found = execSync(cmd, { timeout: 3000 }).toString().trim().split('\n')[0].trim();
    if (found) return found;
  } catch { /* not in PATH */ }
  // ponytail: execFile doesn't search PATH — try common locations
  const common = process.platform === 'win32'
    ? ['terraform.exe', 'C:\\Program Files\\terraform\\terraform.exe', 'C:\\terraform\\terraform.exe']
    : ['/usr/bin/terraform', '/usr/local/bin/terraform', '/opt/homebrew/bin/terraform', 'terraform'];
  for (const p of common) {
    try { execFileSync(p, ['--version'], { timeout: 5000, stdio: 'ignore' }); return p; } catch {}
  }
  return 'terraform'; // last resort — will fail clearly if unreachable
}

export interface PlanDriftResult {
  address: string;           // e.g. "aws_instance.demo_server"
  type: string;              // e.g. "aws_instance"
  name: string;              // e.g. "demo_server"
  actions: string[];         // ["update"] | ["create","delete"] | ["delete"]
  desiredState: Record<string, any>;
  actualState: Record<string, any>;
  changedFields: { field: string; expected: any; actual: any }[];
}

export interface PlanDriftOutcome {
  results: PlanDriftResult[];
  exitCode: number | null;        // 0=no drift, 2=drift, 1=error, null=exec failure
  error?: string;                 // set when exitCode is 1 or null
  stderr?: string;
}

export async function terraformPlanDrift(tfDir?: string): Promise<PlanDriftOutcome> {
  return new Promise((resolve) => {
    const dir = tfDir || process.env.TERRAFORM_DIR || './terraform/ec2';
    let settled = false;

    // Write plan binary to temp file, then read structured JSON from it.
    // The streaming -json format does NOT include before/after values in
    // resource_drift or planned_change events — only the structured output
    // from "terraform show -json <planfile>" carries the actual state diffs.
    const planFile = path.join(os.tmpdir(), `tfplan-${Date.now()}`);
    const tfPath = findTerraform();

    const child = execFile(tfPath,
      ['plan', `-out=${planFile}`, '-detailed-exitcode', '-no-color'],
      { cwd: dir, maxBuffer: 10 * 1024 * 1024, timeout: 120000 },
      (execErr, _stdout, stderr) => {
        if (settled) return;
        settled = true;

        const planExitCode: number | null = execErr ? ((execErr as any).code ?? null) : 0;

        // ── Exit code 1: terraform error ──
        if (planExitCode === 1 || planExitCode === null) {
          const errorMsg = stderr?.slice(0, 500) || execErr?.message || 'Unknown terraform error';
          console.error(`[aws] terraform plan FAILED (exit=${planExitCode}): ${errorMsg}`);
          cleanupPlanFile(planFile);
          resolve({ results: [], exitCode: planExitCode, error: errorMsg, stderr: stderr || '' });
          return;
        }

        // Run terraform show -json <planFile> to get structured output with real before/after
        execFile(tfPath, ['show', '-json', planFile],
          { maxBuffer: 50 * 1024 * 1024, timeout: 30000 },
          (showErr, showStdout, showStderr) => {
            cleanupPlanFile(planFile);

            if (showErr) {
              console.error(`[aws] terraform show FAILED: ${showErr.message}`);
              resolve({ results: [], exitCode: planExitCode, error: `terraform show failed: ${showErr.message}` });
              return;
            }

            try {
              const plan: any = JSON.parse(showStdout);

              // ── DEBUG: dump raw plan to fixed path for investigation ──
              const debugPath = path.join(os.tmpdir(), 'drift-plan-debug.json');
              try { fs.writeFileSync(debugPath, showStdout, 'utf-8'); console.log(`[aws][debug] raw plan dumped to ${debugPath}`); } catch {}

              // ── DEBUG: log ec2_from_bastion in raw resource_changes ──
              for (const rc of (plan.resource_changes || [])) {
                if ((rc.address || '').includes('ec2_from_bastion')) {
                  console.log(`[aws][debug] RAW resource_changes ec2_from_bastion:`, JSON.stringify({ address: rc.address, type: rc.type, actions: rc.change?.actions, before: rc.change?.before, after: rc.change?.after }, null, 2));
                }
              }
              // ── DEBUG: log ec2_from_bastion in raw resource_drift ──
              for (const rd of (plan.resource_drift || [])) {
                if ((rd.address || '').includes('ec2_from_bastion')) {
                  console.log(`[aws][debug] RAW resource_drift ec2_from_bastion:`, JSON.stringify({ address: rd.address, type: rd.type, actions: rd.change?.actions, before: rd.change?.before, after: rd.change?.after }, null, 2));
                }
              }

              const results: PlanDriftResult[] = [];
              const seenAddresses = new Set<string>();

              // ── Process resource_changes (structured output) ──────────
              // before = current actual state, after = desired config state
              const changeByAddress = new Map<string, {
                desiredState: Record<string, any>;
                actualState: Record<string, any>;
                changedFields: { field: string; expected: any; actual: any }[];
              }>();
              for (const rc of (plan.resource_changes || [])) {
                const actions: string[] = rc.change?.actions || [];
                if (actions.length === 0 || actions.includes('no-op')) continue;
                const addr: string = rc.address || '';
                const rtype: string = rc.type || '';
                const rname: string = rc.name || addr.split('.').pop() || '';
                const desiredState = rc.change?.after || {};
                const actualState = rc.change?.before || {};
                // ponytail: desired (after) = expected, live (before) = actual
                const changedFields = (rc.change?.after_unknown || []).length > 0
                  ? [{ field: '_after_unknown', expected: 'known', actual: 'computed' }]
                  : extractChangedFields(rc.change?.after || {}, rc.change?.before || {});
                // ── DEBUG ──
                if (addr.includes('ec2_from_bastion')) {
                  console.log(`[aws][debug] PROCESSED resource_changes ec2_from_bastion: desiredState.to_port=${desiredState?.to_port}, actualState.to_port=${actualState?.to_port}, changedFields=`, JSON.stringify(changedFields));
                }
                changeByAddress.set(addr, { desiredState, actualState, changedFields });
                seenAddresses.add(addr);
                results.push({
                  address: addr, type: rtype, name: rname, actions,
                  desiredState, actualState, changedFields,
                });
              }

              // ── Process resource_drift (structured output) ───────────
              // before = state-file value (desired), after = live AWS value (actual)
              for (const rd of (plan.resource_drift || [])) {
                const actions: string[] = rd.change?.actions || [];
                if (actions.length === 0 || actions.includes('no-op')) continue;
                const addr: string = rd.address || '';
                // ── DEBUG ──
                if (addr.includes('ec2_from_bastion')) {
                  console.log(`[aws][debug] PROCESSING resource_drift ec2_from_bastion: seenAddresses.has=${seenAddresses.has(addr)}, before.to_port=${rd.change?.before?.to_port}, after.to_port=${rd.change?.after?.to_port}`);
                }
                if (seenAddresses.has(addr)) continue;
                const rtype: string = rd.type || '';
                const rname: string = rd.name || addr.split('.').pop() || '';
                const before = rd.change?.before || {};  // state-file (desired)
                const after  = rd.change?.after  || {};  // live AWS (actual)
                const changedFields = extractChangedFields(before, after);
                // ── DEBUG ──
                if (addr.includes('ec2_from_bastion')) {
                  console.log(`[aws][debug] PROCESSED resource_drift ec2_from_bastion: desiredState.to_port=${before?.to_port}, actualState.to_port=${after?.to_port}, changedFields=`, JSON.stringify(changedFields));
                }
                seenAddresses.add(addr);
                results.push({
                  address: addr, type: rtype, name: rname, actions,
                  desiredState: before,    // state-file = desired
                  actualState: after,      // live AWS = actual
                  changedFields,
                });
              }

              // Dump structured plan for debugging
              const ts = new Date().toISOString().replace(/[:.]/g, '-');
              const dumpPath = path.join(os.tmpdir(), `plan-events-${ts}.json`);
              try {
                fs.mkdirSync(path.dirname(dumpPath), { recursive: true });
                fs.writeFileSync(dumpPath, showStdout, 'utf-8');
                console.log(`[aws] plan events dumped to ${dumpPath}; resource_changes=${plan.resource_changes?.length || 0}, resource_drift=${plan.resource_drift?.length || 0}`);
              } catch (writeErr: any) {
                console.error(`[aws] failed to write plan events: ${writeErr.message}`);
              }

              console.log(`[aws] terraform plan exit=${planExitCode}, drift=${results.length} resources`);
              resolve({ results, exitCode: planExitCode });
            } catch (e: any) {
              console.error(`[aws] Failed to parse terraform show output: ${e.message}`);
              resolve({ results: [], exitCode: planExitCode, error: `Parse error: ${e.message}` });
            }
          });
      });

    // Timeout guard
    setTimeout(() => {
      if (settled) return;
      settled = true;
      try { child.kill(); } catch { /* already dead */ }
      console.error(`[aws] terraform plan TIMED OUT after 120s`);
      resolve({ results: [], exitCode: null, error: 'Terraform plan timed out after 120s' });
    }, 125000);
  });
}

function extractChangedFields(before: Record<string, any>, after: Record<string, any>, prefix = ''): { field: string; expected: any; actual: any }[] {
  const changes: { field: string; expected: any; actual: any }[] = [];
  const beforeKeys = Object.keys(before || {});
  const afterKeys = Object.keys(after || {});
  // Build a unique key list without using Set/iteration of Set to avoid TS2802.
  const allKeys = beforeKeys.concat(afterKeys.filter(k => beforeKeys.indexOf(k) === -1));
  for (const key of allKeys) {
    const path = prefix ? `${prefix}.${key}` : key;
    const bVal = before?.[key];
    const aVal = after?.[key];
    if (bVal === aVal) continue;
    if (typeof bVal === 'object' && typeof aVal === 'object' && bVal !== null && aVal !== null && !Array.isArray(bVal)) {
      changes.push(...extractChangedFields(bVal, aVal, path));
    } else {
      changes.push({ field: path, expected: bVal, actual: aVal });
    }
  }
  return changes;
}

// ── Fallback: EC2 describe (lightweight, used when terraform not available) ─

export async function describeActualResources(): Promise<Record<string, any>[]> {
  const ec2 = getEC2();
  if (!ec2) return [];
  const results: Record<string, any>[] = [];

  try {
    const sgResp = await ec2.send(new DescribeSecurityGroupsCommand({}));
    for (const sg of sgResp.SecurityGroups || []) {
      results.push({
        id: `aws_security_group_${sg.GroupName || sg.GroupId}`.replace(/[^a-zA-Z0-9_-]/g, "_"),
        name: sg.GroupName || sg.GroupId || "unknown",
        type: "aws_security_group", service: "VPC",
        actualState: { group_name: sg.GroupName, description: sg.Description },
      });
    }
    const instResp = await ec2.send(new DescribeInstancesCommand({}));
    for (const r of instResp.Reservations || []) {
      for (const inst of r.Instances || []) {
        const nameTag = inst.Tags?.find(t => t.Key === "Name")?.Value || inst.InstanceId;
        results.push({
          id: `aws_instance_${inst.InstanceId}`,
          name: nameTag || "unknown", type: "aws_instance", service: "EC2",
          actualState: { instance_type: inst.InstanceType, ami: inst.ImageId, state: inst.State?.Name, subnet_id: inst.SubnetId, vpc_id: inst.VpcId },
        });
      }
    }
  } catch (err: any) {
    console.log(`[aws] EC2 describe fallback failed: ${err.message}`);
  }
  return results;
}

// ── Cost Explorer ───────────────────────────────────────────────────

export interface CostEstimate {
  monthlyCost: string;
  serviceBreakdown: { service: string; amount: string }[];
  estimatedFineRange: string;
  source: "aws-cost-explorer" | "fallback";
}

export async function getCostEstimate(resourceType: string, riskLevel: string): Promise<CostEstimate> {
  const ce = getCostExplorer();
  if (!ce) return fallbackCostEstimate(resourceType, riskLevel);

  try {
    const today = new Date();
    const start = new Date(today);
    start.setDate(start.getDate() - 30);
    const resp = await ce.send(new GetCostAndUsageCommand({
      TimePeriod: {
        Start: start.toISOString().split("T")[0],
        End: today.toISOString().split("T")[0],
      },
      Granularity: "MONTHLY",
      Metrics: ["UnblendedCost"],
      GroupBy: [{ Type: "DIMENSION", Key: "SERVICE" }],
    }));

    const breakdown = (resp.ResultsByTime || [])
      .flatMap(r => (r.Groups || []).map(g => ({
        service: g.Keys?.[0] || "Unknown",
        amount: g.Metrics?.UnblendedCost?.Amount || "0",
      })))
      .filter(b => parseFloat(b.amount) > 0);

    const total = breakdown.reduce((s, b) => s + parseFloat(b.amount), 0).toFixed(2);
    return {
      monthlyCost: `$${total}`,
      serviceBreakdown: breakdown.slice(0, 10),
      estimatedFineRange: riskLevel === "Critical" ? "$250K–$2.4M" : riskLevel === "High" ? "$10K–$150K" : "$0–$5K",
      source: "aws-cost-explorer",
    };
  } catch {
    return fallbackCostEstimate(resourceType, riskLevel);
  }
}

function fallbackCostEstimate(resourceType: string, riskLevel: string): CostEstimate {
  const costs: Record<string, string> = {
    aws_s3_bucket: "$0.023/GB/month",
    aws_security_group: "free",
    aws_iam_role: "free",
    aws_db_instance: "$0.50–$6.00/hour",
    aws_instance: "$0.10–$3.00/hour",
  };
  return {
    monthlyCost: costs[resourceType] || "Unknown",
    serviceBreakdown: [{ service: resourceType, amount: costs[resourceType] || "Unknown" }],
    estimatedFineRange: riskLevel === "Critical" ? "$250K–$2.4M" : riskLevel === "High" ? "$10K–$150K" : "$0–$5K",
    source: "fallback",
  };
}
