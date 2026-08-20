# PowerShell test command for the refactored GitHub webhook handler
# 
# Prerequisites:
# 1. Environment must have a repo_url set (e.g., "https://github.com/owner/repo.git")
# 2. Per-environment webhook_secret must be set in environment_secrets table
#    OR fall back to global GITHUB_TOKEN will be used
# 3. Dashboard server running on localhost:8000

# Configuration
$endpoint = "http://localhost:8000/api/webhooks/github"
$repo_full_name = "owner/repo"  # Must match the github_full_name from webhook payload
$webhook_secret = "your-webhook-secret"  # Or use global GITHUB_TOKEN as fallback
$pr_number = 123
$pr_title = "[scope-a] Drift fix for EC2 instances"  # Must contain "Drift fix" and [scope-a]

# Construct the PR payload
$payload = @{
    action = "closed"
    pull_request = @{
        number = $pr_number
        title = $pr_title
        merged = $true
        merged_at = (Get-Date -AsUTC).ToString("o")
        merge_commit_sha = "abc123def456"
    }
    repository = @{
        full_name = $repo_full_name
    }
} | ConvertTo-Json -Compress

# Calculate HMAC-SHA256 signature
$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
$hmac = [System.Security.Cryptography.HMACSHA256]::new([System.Text.Encoding]::UTF8.GetBytes($webhook_secret))
$signature = $hmac.ComputeHash($bytes) | ForEach-Object { "{0:x2}" -f $_ }

# Invoke webhook
$headers = @{
    "Content-Type" = "application/json"
    "X-Hub-Signature-256" = "sha256=$signature"
}

$response = Invoke-WebRequest -Uri $endpoint -Method POST -Body $payload -Headers $headers -SkipHttpErrorCheck

Write-Host "Status Code: $($response.StatusCode)"
Write-Host "Response: $($response.Content)"

# Expected behavior:
# - Valid signature + merged PR with "Drift fix" title + repo matches environment:
#   Returns 200 OK with {"ok": true}
# - No environment found for repo OR invalid signature:
#   Returns 401 Unauthorized (same response for both, to avoid info leak)
# - Unmerged PR or missing "Drift fix" title:
#   Returns 204 No Content (silent no-op)
