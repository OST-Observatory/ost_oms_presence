#!/usr/bin/env bash
set -euo pipefail

# Create persistent state directory for observatory_presence and set ownership
# Usage: sudo ./setup_data_dir.sh ost-status

SERVICE_USER="${1:-ost-status}"
STATE_DIR="/var/lib/observatory_presence"

install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${STATE_DIR}"
echo "Created ${STATE_DIR} owned by ${SERVICE_USER}:${SERVICE_USER}"


