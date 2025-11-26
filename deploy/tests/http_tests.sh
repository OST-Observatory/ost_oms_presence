#!/usr/bin/env bash
set -euo pipefail

# Simple E2E test script against the Apache front door
# Usage:
#   BASE_URL=https://observatory.example.org TOKEN=CHANGE_ME ./http_tests.sh

: "${BASE_URL:?BASE_URL is required}"
: "${TOKEN:?TOKEN is required}"

echo "GET /status"
curl -fsSL "$BASE_URL/status" | jq .

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

echo "Done."


