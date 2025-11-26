## Observatory User Presence Monitoring System

Lightweight presence/slot system for an observatory PC. A Windows client prompts the user and sends heartbeats to a central web server that displays who is currently observing.

This README covers:
- Local quickstart (for dev/testing on Windows)
- Production deployment on Debian: Apache (HTTPS) → Gunicorn (UNIX socket) → Flask app
- Windows client setup (PowerShell, Task Scheduler)

---
## Local quickstart (Windows, for testing)
- Windows 11 with Python 3 and pip

1) Install Flask:
```powershell
pip install flask
```
2) Place `observatory_presence.py` somewhere (e.g., `C:\observatory_presence`) and run:
```powershell
cd C:\observatory_presence
python observatory_presence.py
```
3) Open `http://localhost:5000` to see the UI.
4) Optional client test:
   - Place `autostart_client_prompt.ps1` in the same folder
   - Run:
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\autostart_client_prompt.ps1
     ```

---
## Production deployment (Debian, Apache reverse proxy to Gunicorn over UNIX socket)

Architecture:
- Apache serves HTTPS and reverse proxies to Gunicorn via a UNIX socket (`/run/observatory_presence/gunicorn.sock`).
- Gunicorn runs the Flask app with a single worker (the app has a cleaner thread).
- The app uses a shared-secret token to protect mutating endpoints (`/start`, `/heartbeat`, `/release`). The token is expected in the `Authorization: Bearer <TOKEN>` header.
- Application state persists to `presence.json` (default path configurable).

### 1) Prerequisites
- Debian/Ubuntu with Apache:
  ```bash
  sudo apt update
  sudo apt install -y apache2 python3 python3-pip python3-venv fail2ban
  sudo a2enmod proxy proxy_http headers ssl
  ```
- TLS certificate (e.g., via Let's Encrypt) present on the server.

### 2) Create service user and directories
```bash
sudo adduser --system --group --home /nonexistent --no-create-home ost-status || true
sudo install -d -o ost-status -g ost-status /mnt/data/observatory_presence
```

### 3) Deploy the application code
Copy the repository contents into `/mnt/data/observatory_presence` (owner `ost-status:ost-status`).

Option A (system Python, simple):
```bash
sudo pip3 install --upgrade flask gunicorn
```
Option B (recommended: venv):
```bash
sudo -u ost-status python3 -m venv /mnt/data/observatory_presence/.venv
sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install --upgrade pip
sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install flask gunicorn
```
If using venv, adjust `ExecStart` in the systemd unit to point to the venv’s `gunicorn` binary.

### 4) Configure environment and data directory
Create an env file with a strong token:
```bash
sudo install -d -o ost-status -g ost-status /mnt/data/observatory_presence/config
sudo cp /mnt/data/observatory_presence/deploy/systemd/observatory_presence.env.example /mnt/data/observatory_presence/config/observatory_presence.env
sudo nano /mnt/data/observatory_presence/config/observatory_presence.env
# Set:
# SECRET_TOKEN=<long_random_token>
# DATA_FILE=/var/lib/observatory_presence/presence.json
# HEARTBEAT_TIMEOUT=90
# BASE_PATH=/ost_status
```
Create the persistent data directory:
```bash
sudo bash /mnt/data/observatory_presence/deploy/scripts/setup_data_dir.sh ost-status
```

### 5) Systemd service (Gunicorn on UNIX socket)
Install and enable the service:
```bash
sudo cp /mnt/data/observatory_presence/deploy/systemd/observatory_presence.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now observatory_presence
sudo systemctl status observatory_presence
```
The service binds to `unix:/run/observatory_presence/gunicorn.sock`.

### 6) Apache virtual host (mounted under /ost_status)
Install the site configuration:
```bash
sudo cp /mnt/data/observatory_presence/deploy/apache/observatory_presence.conf /etc/apache2/sites-available/
sudo nano /etc/apache2/sites-available/observatory_presence.conf
# Update ServerName and certificate paths.
sudo a2ensite observatory_presence
sudo systemctl reload apache2
```

The provided vhost proxies:
```apache
ProxyPass        /ost_status unix:/run/observatory_presence/gunicorn.sock|http://localhost/
ProxyPassReverse /ost_status http://localhost/
```

### 7) Fail2ban (optional, recommended)
Blocks repeated unauthorized attempts (multiple 401s on POST).
```bash
sudo cp /mnt/data/observatory_presence/deploy/fail2ban/filter.d/observatory_presence.conf /etc/fail2ban/filter.d/
sudo cp /mnt/data/observatory_presence/deploy/fail2ban/jail.d/observatory_presence.local /etc/fail2ban/jail.d/
sudo systemctl restart fail2ban
sudo fail2ban-client status observatory_presence
```
The jail watches `/var/log/apache2/access.log`.

### 8) Smoke tests (mounted under /ost_status)
Simple end-to-end checks (requires `jq`):
```bash
sudo apt install -y jq
BASE_URL=https://observatory.example.org/ost_status TOKEN=<your_token> \
  bash /mnt/data/observatory_presence/deploy/tests/http_tests.sh
```
Manual curl:
```bash
curl -sSL https://observatory.example.org/ost_status/status | jq .
curl -sS -X POST https://observatory.example.org/ost_status/start \
  -H "Authorization: Bearer <your_token>" \
  -d "user=alice&target=saturn"
curl -sS -X POST https://observatory.example.org/ost_status/heartbeat \
  -H "Authorization: Bearer <your_token>" \
  -H "Content-Type: application/json" \
  -d '{"user":"alice"}'
curl -sS -X POST https://observatory.example.org/ost_status/release \
  -H "Authorization: Bearer <your_token>"
```

---
## Windows client (PowerShell + Task Scheduler)

Script: `autostart_client_prompt.ps1`
- Set the central server URL:
  ```powershell
  $Server = "https://observatory.example.org/ost_status"
  ```
- Provide the shared-secret token via environment variable for the user account:
  ```powershell
  setx OBS_PRESENCE_TOKEN "<your_token>"
  ```
  The script reads `$env:OBS_PRESENCE_TOKEN` and sends `Authorization: Bearer <token>`.
 - Optional: set server via environment variable (persistent for the user):
   ```powershell
   setx OBS_PRESENCE_SERVER "https://observatory.example.org/ost_status"
   ```

Run once (bypassing policy):
```powershell
powershell -ExecutionPolicy Bypass -File "C:\observatory_presence\autostart_client_prompt.ps1"
```

Task Scheduler setup:
1. Open “Task Scheduler”.
2. New Task:
   - Name: “Observatory Presence Client”
   - Trigger: “At log on”
   - Action: Program/script: `powershell.exe`
   - Arguments: `-ExecutionPolicy Bypass -File "C:\observatory_presence\autostart_client_prompt.ps1"`
   - Alternative (pass token inline without persistent env var):
     - Arguments:
       ```
       -ExecutionPolicy Bypass -NoProfile -Command "$env:OBS_PRESENCE_SERVER='https://observatory.example.org/ost_status'; $env:OBS_PRESENCE_TOKEN='YOUR_LONG_RANDOM_TOKEN'; & 'C:\observatory_presence\autostart_client_prompt.ps1'"
       ```
   - Condition: enable “Run only when user is logged on”

Troubleshooting execution policy:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# or unblock file
Unblock-File -Path "C:\observatory_presence\autostart_client_prompt.ps1"
```

---
## Security notes
- Always use HTTPS on Apache.
- Use a long random `SECRET_TOKEN` and rotate periodically.
- Consider IP allowlisting for POST endpoints if appropriate.
- fail2ban can reduce brute-force attempts; for burst control add `mod_evasive` or app-level rate-limiting if needed.

---
## Files of interest
- `observatory_presence.py` (Flask app)
- `autostart_client_prompt.ps1` (Windows client)
- `deploy/systemd/observatory_presence.service` (systemd unit)
- `deploy/systemd/observatory_presence.env.example` (environment variables)
- `deploy/apache/observatory_presence.conf` (Apache vhost)
- `deploy/fail2ban/filter.d/observatory_presence.conf` and `deploy/fail2ban/jail.d/observatory_presence.local`
- `deploy/scripts/setup_data_dir.sh` (creates data dir with permissions)
- `deploy/tests/http_tests.sh` (basic E2E test)
