/** A deployment environment returned by GET /api/environments */
export interface Environment {
  id:                        number;
  slug:                      string;
  name:                      string;
  is_active:                 boolean;
  aws_account_id:            string;
  region:                    string;
  tf_state_bucket:           string;
  tf_directory_path:         string;
  auth_type:                 'profile' | 'role' | 'keys';
  aws_profile:               string | null;
  aws_role_arn:              string | null;
  aws_external_id:           string | null;
  tf_lock_table:             string | null;
  scan_role_variable:        string | null;
  apply_role_secret_name:    string | null;
  apply_environment_name:    string | null;
  repo_url:                  string | null;
  repo_branch:               string | null;
  git_auth_type:             'none' | 'token';
  github_token_configured:   boolean;
  github_token_masked:       string | null;
  aws_access_key_configured: boolean;
  aws_access_key_masked:     string | null;
  aws_secret_key_configured: boolean;
  aws_secret_key_masked:     string | null;
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
