#!/usr/bin/env bash
# One-shot setup for running the Drift Reconciler dashboard + agent on a
# fresh Amazon Linux 2023 / Ubuntu EC2 instance.
#
# Usage:  sudo bash scripts/ec2_setup.sh
# After:  copy .env into the repo root, then start the dashboard (see end).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 1. System tools ────────────────────────────────────────────────────
if command -v apt-get >/dev/null 2>&1; then
  # Ubuntu / Debian
  apt-get update -y
  apt-get install -y git python3 python3-venv python3-pip curl unzip gnupg

  # Terraform (HashiCorp APT repo)
  if ! command -v terraform >/dev/null 2>&1; then
    wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo "$VERSION_CODENAME") main" \
      > /etc/apt/sources.list.d/hashicorp.list
    apt-get update -y
    apt-get install -y terraform
  fi

  # Trivy (Aqua Security APT repo) — optional: the pipeline proceeds without
  # it, but the Trivy gate / security-scan mode silently no-op without it.
  if ! command -v trivy >/dev/null 2>&1; then
    wget -qO- https://aquasecurity.github.io/trivy-repo/deb/public.key | gpg --dearmor -o /usr/share/keyrings/trivy.gpg
    echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" \
      > /etc/apt/sources.list.d/trivy.list
    apt-get update -y
    apt-get install -y trivy
  fi

elif command -v dnf >/dev/null 2>&1; then
  # Amazon Linux 2023 / Fedora
  dnf install -y git python3 python3-pip
  if ! command -v terraform >/dev/null 2>&1; then
    dnf config-manager --add-repo https://rpm.releases.hashicorp.com/AmazonLinux/hashicorp.repo || true
    dnf install -y terraform || true
  fi
  echo "NOTE: trivy install skipped on dnf — see https://aquasecurity.github.io/trivy/latest/getting-started/installation/"
else
  echo "Unsupported package manager — install git/python3/terraform/trivy manually."
  exit 1
fi

# hcledit is optional (regex fallback exists); install via Go only if wanted.
if ! command -v hcledit >/dev/null 2>&1; then
  echo "NOTE: hcledit not found — the regex HCL patcher will be used. Install from https://github.com/minamijoyo/hcledit/releases for more reliable patching."
fi

# ── 2. Python venv + dependencies ──────────────────────────────────────
cd "$REPO_ROOT"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ── 3. Start (after .env is in place) ──────────────────────────────────
echo
echo "Setup complete."
echo "1. Copy your .env (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SESSION_SECRET,"
echo "   GITHUB_TOKEN, GITHUB_REPO, LLM keys) into: $REPO_ROOT/.env"
echo "2. Start the dashboard:"
echo "   source .venv/bin/activate && python dashboard/serve.py --port 8080"
echo "   (or run under systemd/nohup for a persistent service)"
