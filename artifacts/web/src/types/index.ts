/** A deployment environment returned by GET /api/environments */
export interface Environment {
  slug: string;
  name: string;
  is_active: boolean;
  [key: string]: unknown;
}

/** A single drift event row from the drift_events table */
export interface DriftEvent {
  id: number;
  created_at: string;
  account: string;
  region: string | null;
  resource_id: string;
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  pr_number: number | null;
  pr_type: 'fix' | 'batch' | 'rollback' | 'unmanaged' | 'manual' | null;
  status: 'open' | 'resolved' | 'suppressed';
  resolution: string | null;
  fields_changed: string[] | null;
  drift_summary: string | null;
  changes_jsonb: Record<string, { before: unknown; after: unknown }> | null;
  file_path: string | null;
  unmanaged: boolean | null;
  cost_impact: { monthly_estimate_usd?: number; [key: string]: unknown } | null;
  trivy_passed: boolean | null;
  trivy_summary: string | null;
}
