#!/usr/bin/env bash
set -euo pipefail

echo "=== Hunter build: starting ==="

# ── Step 1: Install Node.js if not present ───────────────────────────────────
# Render's Python runtime does not include Node.js by default.
if ! command -v node &>/dev/null; then
  echo "Node.js not found — installing via apt..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq nodejs npm
fi

echo "Node: $(node --version)  npm: $(npm --version)"

# ── Step 2: Build frontend ────────────────────────────────────────────────────
echo "=== Building frontend ==="
cd frontend
npm ci
npm run build
cd ..

# ── Step 3: Copy dist to backend serving location ────────────────────────────
echo "=== Copying frontend dist to backend/frontend_dist ==="
rm -rf backend/frontend_dist
cp -r frontend/dist backend/frontend_dist

# ── Step 4: Install Python dependencies ──────────────────────────────────────
echo "=== Installing Python dependencies ==="
pip install -r backend/requirements.txt

echo "=== Hunter build: complete ==="
