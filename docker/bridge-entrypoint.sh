#!/usr/bin/env bash
# Copy host Claude credentials (mounted read-only at /host-claude) into the
# container HOME, then start the CLI bridge. Re-copying on every start keeps
# the OAuth token fresh as long as the host's Claude Code login is current.
set -e

mkdir -p /root/.claude

if [ -f /host-claude/.credentials.json ]; then
  cp -f /host-claude/.credentials.json /root/.claude/.credentials.json
  echo "[entrypoint] copied claude credentials from /host-claude"
else
  echo "[entrypoint] WARNING: /host-claude/.credentials.json not found — claude will be unauthenticated"
fi

# Optional: carry over settings.json if present (model prefs etc.)
if [ -f /host-claude/settings.json ]; then
  cp -f /host-claude/settings.json /root/.claude/settings.json || true
fi

exec node /app/bridge-server.mjs
