#!/usr/bin/env bash
set -euo pipefail

echo "=== Hunter build: starting ==="

# ── Step 1: Install Node.js 20 LTS (Render Python runtime ships without Node) ─
# apt-get on Ubuntu 22.04 defaults to Node 12 which is too old for Vite 5.
# We pull from NodeSource to guarantee Node 20 LTS.
REQUIRED_MAJOR=18
CURRENT_MAJOR=0
if command -v node &>/dev/null; then
  CURRENT_MAJOR=$(node --version 2>/dev/null | sed 's/v//' | cut -d'.' -f1 || echo 0)
fi

if [ "$CURRENT_MAJOR" -lt "$REQUIRED_MAJOR" ]; then
  echo "Node.js not found or too old (v${CURRENT_MAJOR}) — installing Node 20 LTS via NodeSource..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>&1 | tail -5
  apt-get install -y nodejs
else
  echo "Node.js v${CURRENT_MAJOR} already present — skipping install"
fi

echo "Node: $(node --version)  npm: $(npm --version)"

# ── Step 2: Build frontend ────────────────────────────────────────────────────
echo "=== Building frontend ==="
cd frontend
npm ci --prefer-offline 2>/dev/null || npm ci
npm run build
cd ..

# ── Step 3: Copy dist to backend serving location ────────────────────────────
echo "=== Copying frontend dist to backend/frontend_dist ==="
rm -rf backend/frontend_dist
cp -r frontend/dist backend/frontend_dist
echo "frontend_dist contents: $(ls backend/frontend_dist)"

# ── Step 4: Install Python dependencies ──────────────────────────────────────
echo "=== Installing Python dependencies ==="
pip install -r backend/requirements.txt

echo "=== Hunter build: complete ==="
