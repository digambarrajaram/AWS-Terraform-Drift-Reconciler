export type RiskLevel = 'Low' | 'Medium' | 'High' | 'Critical';
export type DriftType = 'high_risk_change' | 'moderate_risk_change' | 'low_risk_change' | 'none';
export type PRStatus = 'Open' | 'Merged' | 'Closed' | 'Rejected';
export type Environment = 'demo' | 'staging' | 'production';

export interface AwsResource {
  id: string;
  name: string;
  type: string;
  service: string;
  desiredState: Record<string, any>;
  actualState: Record<string, any>;
  isDrifted: boolean;
  driftDetails?: {
    field: string;
    expected: any;
    actual: any;
    severity: RiskLevel;
  }[];
  terraformCode: string;
  lastChecked: string;
}

export interface DriftAnalysis {
  resourceId: string;
  classification: DriftType;
  riskScore: RiskLevel;
  explanation: string;
  securityImpact: string;
  costImpact: string;
  hclFix: string;
  hclDiff?: string;
  fixType?: string;
  correctionAttempts: number;
  diffHash?: string;
  policyReferences?: { id: string; name: string; severity: string; source?: string }[];
  checkovChecks?: { id?: string; name?: string; severity?: string; status?: string; source?: string; impact?: string }[];
  checkovSummary?: string;
  validationStatus?: 'passed' | 'failed' | 'pending';
  validationOutput?: any;
}

export interface PullRequest {
  id: string;
  number: number;
  title: string;
  branch: string;
  description: string;
  status: PRStatus;
  createdAt: string;
  mergedAt?: string;
  rejectedAt?: string;
  rejectedBy?: string;
  rejectionReason?: string;
  approvedBy?: string;
  approvedAt?: string;
  hclChanges: string;
  analysis: DriftAnalysis;
}

export interface TimelineEvent {
  id: string;
  timestamp: string;
  type: 'scan_clean' | 'scan_drift' | 'pr_created' | 'pr_merged' | 'pr_rejected' | 'arn_reveal' | 'reset';
  title: string;
  message: string;
  resourceId?: string;
  details?: Record<string, any>;
}

export interface AuditRecord {
  id: string;
  timestamp: string;
  action: string;
  resourceId?: string;
  prNumber?: number;
  actor?: string;
  diffHash?: string;
  details?: Record<string, any>;
}

// Secrets NEVER returned to frontend. IntegrationStatus shows only Connected/Not Configured.
export interface IntegrationStatus {
  pagerDuty: 'connected' | 'not_configured';
  github: 'connected' | 'not_configured' | 'simulated';
  aws: 'connected' | 'not_configured';
  terraformState: 'loaded' | 'empty' | 'not_configured';
  lastPagerDutyError?: string;   // shown only if a recent send failed
  lastGitHubError?: string;      // shown only if a recent PR operation failed
}

// AlertConfig — routing key stays server-side only. Frontend only sees enabled flag.
export interface AlertConfig {
  enabled: boolean;
}

export interface SystemState {
  environment: Environment;
  resources: AwsResource[];
  prs: PullRequest[];
  timeline: TimelineEvent[];
  lastScanTime: string | null;
  scanning: boolean;
  alertConfig?: AlertConfig;
  integrationStatus: IntegrationStatus;
  schedulerHealthy: boolean;
  maskAccountIds: boolean;
}
