#!/bin/bash
set -euo pipefail
CM="http://127.0.0.1:8787"

# if CM not ready -> nothing
curl -fsS --max-time 1 "$CM/health" | grep -q '"ready":true' || exit 0

# if no circuit -> nudge by hitting endpoints (keeps cache logic hot)
n="$(curl -fsS --max-time 2 "$CM/circuit" | jq -r '.hops|length' 2>/dev/null || echo 0)"
if [ "${n:-0}" -lt 2 ]; then
  curl -fsS --max-time 2 "$CM/circuits?order=desc" >/dev/null || true
  curl -fsS --max-time 2 "$CM/circuit" >/dev/null || true
fi
