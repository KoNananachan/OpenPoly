#!/usr/bin/env bash
# Push the current local worktree to a remote VPS and restart the backend.
# Only relevant for the separated (geoblock) deployment — see
# docs/deploy/separated-deployment.md.
#
# Idempotent — re-run any time after committing.  Skips uv sync when neither
# pyproject.toml nor uv.lock changed (the hot path: ~3s end-to-end).
#
# Requires:  key-based ssh to the `openpoly-vps` host alias (~/.ssh/config) and
#            the systemd unit openpoly.service installed — both documented in
#            docs/deploy/separated-deployment.md.

set -euo pipefail

HOST=openpoly-vps
DEST=/opt/openpoly

# Single source of truth for exclude rules — keep cold-deploy + this in sync.
# Exclude == "do not transfer AND (if --delete were used) do not delete remote".
# We don't use --delete (too easy to nuke VPS-only artefacts like .env).
RSYNC_EXCLUDES=(
  --exclude='.venv/'
  --exclude='.git/'
  --exclude='.env'                       # VPS-only secrets (gitignored)
  --exclude='frontend/node_modules/'
  --exclude='frontend/dist/'
  --exclude='frontend/.vite/'
  --exclude='**/__pycache__/'
  --exclude='**/.pytest_cache/'
  --exclude='**/*.pyc'
  --exclude='.claude/'
  --exclude='.DS_Store'
  --exclude='*.log'
  --exclude='openpoly.db*'
)

# Detect dependency changes before doing the heavy rsync so we only pay the
# uv-sync cost (~5min) when it's actually needed.
DEPS_CHANGED=0
if rsync --dry-run --itemize-changes \
     "${RSYNC_EXCLUDES[@]}" \
     --include='pyproject.toml' --include='uv.lock' --include='*/' \
     --exclude='*' \
     ./ "${HOST}":"${DEST}/" 2>/dev/null | grep -qE '^>f.*\b(pyproject\.toml|uv\.lock)$'; then
  DEPS_CHANGED=1
fi

echo "→ rsync ./ → ${HOST}:${DEST}/  (transfer-only, no --delete)"
rsync -avz "${RSYNC_EXCLUDES[@]}" ./ "${HOST}":"${DEST}/"

if [ "${DEPS_CHANGED}" = "1" ]; then
  echo "→ pyproject/uv.lock changed: running uv sync on VPS"
  ssh "${HOST}" 'cd /opt/openpoly && ~/.local/bin/uv sync && \
    rm -rf .venv/lib/python3.14/site-packages/{nvidia,triton}* 2>/dev/null || true && \
    ~/.local/bin/uv cache clean'
fi

echo "→ systemctl restart openpoly"
ssh "${HOST}" 'systemctl restart openpoly'

echo "→ wait for health"
for i in $(seq 1 20); do
  if ssh "${HOST}" 'curl -sS --max-time 2 http://127.0.0.1:18000/api/health' 2>/dev/null | grep -q '"status":"ok"'; then
    echo "✅ deployed; health 200 after ${i}s"
    exit 0
  fi
  sleep 1
done

echo "❌ health check failed within 20s; check 'ssh ${HOST} journalctl -u openpoly -n 50'"
exit 1
