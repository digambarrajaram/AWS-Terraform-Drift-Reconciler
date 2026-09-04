# syntax=docker/dockerfile:1

# ── Stage 1: build React/Vite frontend ───────────────────────────────────────
# Node 22 satisfies Vite 7 (engines: ^20.19.0 || >=22.12.0). Project uses pnpm
# (no package-lock.json); frozen lockfile is the reproducible install equivalent.
FROM node:22-slim AS frontend-builder

WORKDIR /build/frontend

# Pin pnpm to match local dev. pnpm 11 requires allowBuilds in pnpm-workspace.yaml.
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/tsconfig.base.json ./
COPY frontend/artifacts/web ./artifacts/web

ENV CI=true \
    NODE_ENV=production
RUN pnpm install --frozen-lockfile --filter @workspace/web...
RUN pnpm --filter @workspace/web run build

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
# README requires Python 3.11+; 3.12-slim is a stable production baseline.
FROM python:3.12-slim AS runtime

ARG TERRAFORM_VERSION=1.9.8
ARG TRIVY_VERSION=0.74.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DRIFT_CLONE_BASE=/tmp/drift-clones

WORKDIR /app

# git: clone customer Terraform repos at scan time
# curl: HEALTHCHECK against /api/config
# ca-certificates: HTTPS to Supabase / GitHub / AWS APIs
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        unzip \
    && curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
        -o /tmp/terraform.zip \
    && unzip /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip \
    && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
        | sh -s -- -b /usr/local/bin "v${TRIVY_VERSION}" \
    && chmod 755 /usr/local/bin/trivy \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend Python source only (no legacy static assets — React build replaces them).
COPY dashboard/*.py ./dashboard/
COPY drift_reconciler/ ./drift_reconciler/

# Built frontend → dashboard/ (_DASHBOARD_DIR in dashboard/paths.py).
COPY --from=frontend-builder /build/frontend/artifacts/web/dist/public/ ./dashboard/

# serve.py routes legacy paths via per-page HTML names; duplicate the SPA shell so
# deep links like /scan and /explorer work without backend SPA-fallback code.
RUN set -eux; \
    cd /app/dashboard; \
    for page in scan explorer pr-queue rollback trends exceptions alerts environments; do \
        cp -f index.html "${page}.html"; \
    done; \
    rm -f *.js styles.css 2>/dev/null || true

RUN groupadd --system drift \
    && useradd --system --gid drift --create-home --home-dir /home/drift drift \
    && mkdir -p /tmp/drift-clones /tmp/drift-logs \
    && chown -R drift:drift /app /tmp/drift-clones /tmp/drift-logs

USER drift

EXPOSE 8080

# /api/config exists, is JWT-exempt, and returns 200 when Supabase is configured.
# When API_ACCESS_TOKEN is set, the same header the app uses must be supplied.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS \
        ${API_ACCESS_TOKEN:+-H "X-Api-Access-Token: ${API_ACCESS_TOKEN}"} \
        http://127.0.0.1:8080/api/config \
        || exit 1

CMD ["python", "dashboard/serve.py", "--port", "8080"]
