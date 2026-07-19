import sys; sys.path.insert(0, 'drift_reconciler')
from env_loader import load_env; load_env()
import os, requests
url = os.environ['SUPABASE_URL'].rstrip('/')
key = os.environ['SUPABASE_ANON_KEY'] or os.environ['SUPABASE_SERVICE_ROLE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}
r = requests.get(f'{url}/rest/v1/scan_runs?select=id,scope,status,current_stage,result_summary&order=started_at.desc&limit=3', headers=h)
for row in (r.json() or []):
    short_id = str(row.get('id', ''))[:8]
    stage = row.get('current_stage','?')
    summary = str(row.get('result_summary',''))[:60]
    print(f"{short_id}... {row.get('scope','')} {row.get('status',''):12s} {stage:20s} {summary}")
