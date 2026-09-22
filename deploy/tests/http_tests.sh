#!/usr/bin/env bash
set -euo pipefail

# Simple E2E test script against the Apache front door
# Usage:
#   BASE_URL=https://observatory.example.org/ost_status TOKEN=CHANGE_ME ./http_tests.sh
#
# Optionally set LOGIN_USER / LOGIN_PASSWORD to exercise the LDAP dashboard
# login; without them the browser endpoints are only checked for "401/302",
# i.e. that they are in fact protected.

: "${BASE_URL:?BASE_URL is required}"
: "${TOKEN:?TOKEN is required}"
LOGIN_USER="${LOGIN_USER:-}"
LOGIN_PASSWORD="${LOGIN_PASSWORD:-}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "GET /health (public)"
curl -fsSL "$BASE_URL/health" | jq .

echo "GET /datenschutz (public)"
curl -fsSL -o /dev/null -w "  %{http_code}\n" "$BASE_URL/datenschutz"

echo "GET /status without a session (must be 401)"
code=$(curl -sSL -o /dev/null -w '%{http_code}' "$BASE_URL/status")
if [ "$code" != "401" ]; then
  echo "  FAIL: expected 401, got $code -- the dashboard is not protected!" >&2
  exit 1
fi
echo "  401 as expected"

if [ -n "$LOGIN_USER" ] && [ -n "$LOGIN_PASSWORD" ]; then
  echo "POST /login as $LOGIN_USER"
  csrf=$(curl -sSL -c "$COOKIE_JAR" "$BASE_URL/login" \
    | grep -o 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
  [ -n "$csrf" ] || { echo "  FAIL: no CSRF token on the login page" >&2; exit 1; }
  code=$(curl -sS -b "$COOKIE_JAR" -c "$COOKIE_JAR" -o /dev/null -w '%{http_code}' \
    --data-urlencode "csrf_token=$csrf" \
    --data-urlencode "username=$LOGIN_USER" \
    --data-urlencode "password=$LOGIN_PASSWORD" \
    "$BASE_URL/login")
  if [ "$code" != "302" ]; then
    echo "  FAIL: login returned $code (expected 302)" >&2
    exit 1
  fi
  echo "  login ok"

  echo "GET /status with a session"
  curl -fsSL -b "$COOKIE_JAR" "$BASE_URL/status" | jq .
else
  echo "GET /status with a session -- skipped (set LOGIN_USER / LOGIN_PASSWORD)"
fi

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
if [ -n "$LOGIN_USER" ] && [ -n "$LOGIN_PASSWORD" ]; then
  curl -fsSL -b "$COOKIE_JAR" "$BASE_URL/status" | jq -e '.hosts["e2e-host"].hostId == "e2e-host"'
else
  echo "  skipped (needs a dashboard session)"
fi

echo "Done."
