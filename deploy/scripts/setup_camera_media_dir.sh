#!/usr/bin/env bash
set -euo pipefail

# Create directory for webcam stills and videos served under /ost_status/media/cameras/
# Usage: sudo ./setup_camera_media_dir.sh [upload-user]

UPLOAD_USER="${1:-root}"
MEDIA_DIR="/var/lib/observatory_cameras"

install -d -m 0755 -o "${UPLOAD_USER}" -g www-data "${MEDIA_DIR}"
echo "Created ${MEDIA_DIR} (owner ${UPLOAD_USER}:www-data, mode 0755)"
echo "Expected files:"
echo "  outdoor_current.jpg"
echo "  indoor_current.jpg"
echo "  outdoor_video.webm"
echo "  yesterday_outdoor_video.webm"
