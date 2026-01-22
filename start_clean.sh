#!/usr/bin/env bash
set -euo pipefail

# Kill anything bound to the app ports, then start the stack.
PORTS=("5000" "5005" "5055")

if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -tiTCP:${PORTS[*]} -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')
  if [ -n "${PIDS:-}" ]; then
    echo "Killing processes on ports ${PORTS[*]}: ${PIDS}"
    kill ${PIDS} || true
  else
    echo "No listeners on ${PORTS[*]}"
  fi
else
  echo "lsof not found; skipping port cleanup."
fi

echo "Starting stack..."
./run.sh
