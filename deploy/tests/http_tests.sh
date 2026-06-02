#!/usr/bin/env bash
set -euo pipefail

# Simple E2E test script against the Apache front door
# Usage:
#   BASE_URL=https://observatory.example.org/ost_status TOKEN=CHANGE_ME ./http_tests.sh

: "${BASE_URL:?BASE_URL is required}"
: "${TOKEN:?TOKEN is required}"

echo "GET /health"
curl -fsSL "$BASE_URL/health" | jq .

echo "GET /status"
curl -fsSL "$BASE_URL/status" | jq .

echo "POST /host_status (seed monitoring data)"
curl -fsS -X POST "$BASE_URL/host_status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hostId":"e2e-host","cpuPercent":1,"memPercent":2}' | jq .

echo "POST /start"
curl -fsS -X POST "$BASE_URL/start" \
  -H "Authorization: Bearer $TOKEN" \
  -d "user=tester&target=demo" | jq .

echo "POST /heartbeat"
curl -fsS -X POST "$BASE_URL/heartbeat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user":"tester"}' | jq .

echo "Waiting 2 seconds..."
sleep 2

echo "POST /release"
curl -fsS -X POST "$BASE_URL/release" \
  -H "Authorization: Bearer $TOKEN" | jq .

echo "Verify host_status survived release"
curl -fsSL "$BASE_URL/status" | jq -e '.hosts["e2e-host"].hostId == "e2e-host"'

echo "Done."
