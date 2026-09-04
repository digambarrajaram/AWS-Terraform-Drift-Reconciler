# Deploying drift-reconciler on EC2 (Docker)

This guide covers building the image locally or in CI, pushing to Docker Hub, and running on an Ubuntu EC2 instance under `/home/ubuntu/`.

The container runs a single process: `python dashboard/serve.py` on port **8080**, serving the built React UI from `dashboard/` and the API under `/api/*`. All secrets come from a host-side `.env` file passed at runtime — nothing is baked into the image.

---

## 1. Prerequisites on EC2

- Docker Engine installed and the `ubuntu` user in the `docker` group
- Outbound HTTPS (Supabase, GitHub, AWS APIs, LLM providers)
- Supabase migrations under `migrations/` applied manually in the Supabase SQL editor **before** first deploy (the app does not run migrations at startup)

Optional but recommended on the host (not inside the container):

- Reverse proxy (nginx/Caddy) with TLS in front of port 8080
- Security group allowing inbound 8080 (or 443 via proxy) only from trusted IPs

---

## 2. Create runtime environment file on EC2

Create the directory and env file on the host. **Never commit this file or copy it into the image.**

```bash
mkdir -p /home/ubuntu/drift-reconciler
chmod 700 /home/ubuntu/drift-reconciler
nano /home/ubuntu/drift-reconciler/.env
```

### Required variables

These must be set for a working deployment (cross-checked against `.env.example`):

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend service-role key (server-side DB access) |
| `SUPABASE_ANON_KEY` | Public anon key (injected to frontend via `/api/config`) |
| `API_ACCESS_TOKEN` | Shared secret; required header `X-Api-Access-Token` on `/api/*` when set |

### AWS

| Variable | Required | Purpose |
|---|---|---|
| `AWS_REGION` | Yes | Default AWS region |
| `DRIFT_BACKEND_AWS_ACCESS_KEY_ID` | No* | Static keys for the backend identity that calls STS AssumeRole |
| `DRIFT_BACKEND_AWS_SECRET_ACCESS_KEY` | No* | Pair for the above |

\*If omitted, boto3 uses the default credential chain. On EC2 you can attach an instance profile with permission to `sts:AssumeRole` into customer roles instead of setting static keys.

### LLM (set at least one provider)

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq (checked first) |
| `GEMINI_API_KEY` | Google Gemini (second) |
| `AWS_BEDROCK_REGION` | Bedrock region when using Bedrock fallback |
| `AWS_BEDROCK_ACCESS_KEY_ID` | Optional Bedrock-specific credentials |
| `AWS_BEDROCK_SECRET_ACCESS_KEY` | Optional Bedrock-specific pair |

Resolution order at runtime: Groq → Gemini → Bedrock (default credential chain).

### Optional overrides

| Variable | Default | Purpose |
|---|---|---|
| `DRIFT_CLONE_BASE` | `/tmp/drift-clones` in the image | Where customer Terraform repos are cloned. Ephemeral is safe: each scan fetches and hard-resets clones. Override only if you want clones to survive container restarts (see volume note below). |

Per-environment settings (GitHub tokens, notification webhooks, IAM role ARNs, repo URLs, etc.) live in the Supabase database — not in this file.

Example `.env` skeleton (replace placeholders):

```bash
# Supabase
SUPABASE_URL="https://xxxxxxxxxxxx.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="eyJ..."
SUPABASE_ANON_KEY="eyJ..."

# API access control (strongly recommended in production)
API_ACCESS_TOKEN="generate-a-long-random-string"

# LLM — set one
GROQ_API_KEY=""
# GEMINI_API_KEY=""

# AWS
AWS_REGION="us-east-1"
AWS_BEDROCK_REGION="us-east-1"
# DRIFT_BACKEND_AWS_ACCESS_KEY_ID="AKIA..."
# DRIFT_BACKEND_AWS_SECRET_ACCESS_KEY="..."
```

Lock down permissions:

```bash
chmod 600 /home/ubuntu/drift-reconciler/.env
```

---

## 3. Build the image (local machine or CI)

From the repository root:

```bash
docker build -t <dockerhub-username>/drift-reconciler:<tag> .
```

Example:

```bash
docker build -t myuser/drift-reconciler:1.0.0 .
```

---

## 4. Push to Docker Hub

```bash
docker login
docker push <dockerhub-username>/drift-reconciler:<tag>
```

---

## 5. Pull and run on EC2

```bash
docker pull <dockerhub-username>/drift-reconciler:<tag>

docker run -d \
  --name drift-reconciler \
  --restart unless-stopped \
  --env-file /home/ubuntu/drift-reconciler/.env \
  -p 8080:8080 \
  <dockerhub-username>/drift-reconciler:<tag>
```

`--restart unless-stopped` keeps the service up across EC2 reboots.

Open `http://<ec2-public-ip>:8080` (or your TLS proxy URL).

### Optional: persist git clones across restarts

Only needed if you want faster first scan after a container recreate and accept stale-clone risk without the hard-reset path. The default ephemeral `/tmp/drift-clones` is correct for production because clones are refreshed on every run.

```bash
mkdir -p /home/ubuntu/drift-reconciler/clones

docker run -d \
  --name drift-reconciler \
  --restart unless-stopped \
  --env-file /home/ubuntu/drift-reconciler/.env \
  -e DRIFT_CLONE_BASE=/var/lib/drift-clones \
  -v /home/ubuntu/drift-reconciler/clones:/var/lib/drift-clones \
  -p 8080:8080 \
  <dockerhub-username>/drift-reconciler:<tag>
```

---

## 6. Logs

Application logs go to stdout/stderr (viewable with Docker):

```bash
docker logs -f drift-reconciler
```

Scan/rollback subprocess logs are also written ephemerally under `/tmp/drift-logs/` inside the container (cleaned after 24 h). No host volume is required.

---

## 7. Health check

The image `HEALTHCHECK` calls **`GET /api/config`** on port 8080.

- JWT is **not** required for this endpoint.
- When `API_ACCESS_TOKEN` is set, Docker passes `X-Api-Access-Token` from the container environment (supplied via `--env-file`).
- Returns **200** when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are configured; **503** if not.

Manual check from the EC2 host:

```bash
source /home/ubuntu/drift-reconciler/.env
curl -fsS -H "X-Api-Access-Token: ${API_ACCESS_TOKEN}" \
  http://127.0.0.1:8080/api/config
```

---

## 8. Update to a new version

This simple setup stops the old container before starting the new one (brief downtime).

```bash
docker pull <dockerhub-username>/drift-reconciler:<new-tag>
docker stop drift-reconciler
docker rm drift-reconciler

docker run -d \
  --name drift-reconciler \
  --restart unless-stopped \
  --env-file /home/ubuntu/drift-reconciler/.env \
  -p 8080:8080 \
  <dockerhub-username>/drift-reconciler:<new-tag>
```

Zero-downtime blue/green or rolling updates are out of scope for this document.

---

## 9. Known limitations

- **SPA deep links**: `/login`, `/approvals`, `/reset-password`, and other React Router paths are served via SPA fallback (`index.html`). Supabase password-reset emails linking to `/reset-password?<token>` work on cold direct hits.
- **Trivy**: Installed at `/usr/local/bin/trivy` (pinned via `TRIVY_VERSION` build-arg in the Dockerfile).
- **Terraform version**: Image pins Terraform **1.9.8** (matches CI). Rebuild with a different `TERRAFORM_VERSION` build-arg if your state requires another version.
