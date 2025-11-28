#!/usr/bin/env bash
set -euo pipefail

: "${BASE_URL:?BASE_URL is required e.g. https://host/ost_status}"
: "${TOKEN:?TOKEN is required}"

hostId="${HOSTNAME:-test-host}"
now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
payload=$(cat <<JSON
{
  "hostId": "$hostId",
  "ts": "$now",
  "uptimeSec": 12345,
  "cpuPercent": 7.5,
  "memPercent": 42.0,
  "diskCPercent": 80.0,
  "osVersion": "Windows"
}
JSON
)

echo "POST /host_status"
curl -fsS -X POST "$BASE_URL/host_status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$payload" | jq .

echo "GET /status (verify hosts)"
curl -fsSL "$BASE_URL/status" | jq '.hosts'

echo "Done."


