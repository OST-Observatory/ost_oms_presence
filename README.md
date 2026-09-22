## Observatory User Presence Monitoring System (Dashboard)

Lightweight presence/slot system for an observatory PC. A Windows client prompts the user and sends heartbeats to a central web server that displays who is currently observing.

This README covers:
- Development setup on Linux (venv, run modes, tests)
- Production deployment on Debian: Apache (HTTPS) → Gunicorn (UNIX socket) → Flask app
- Windows client setup (PowerShell, Task Scheduler)

---
## Development setup (Linux)

For working on the app locally. The production setup is a different thing entirely — see [Production deployment](#production-deployment-debian-apache-reverse-proxy-to-gunicorn-over-unix-socket) below.

### Prerequisites

Python 3.12 or newer (that is what CI uses), plus the venv module:
```bash
sudo apt install -y python3 python3-venv python3-pip
```
No system libraries are needed. The LDAP client (`ldap3`) is pure Python, so there is nothing to compile and no `libldap2-dev`.

### 1) Virtualenv and dependencies

```bash
git clone <repository-url> ost_oms_presence
cd ost_oms_presence
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
Optional extras:
```bash
.venv/bin/pip install pytest                        # to run the test suite
.venv/bin/pip install -r requirements-astropy.txt   # J2000 coordinate conversion
```

### 2) Pick a run mode

The app refuses to start half-configured, so choose one of these three. All of them write state to `presence.json` in the working directory (gitignored).

**a) No authentication at all — fastest for UI work**
```bash
export ALLOW_OPEN_API=1              # agent endpoints need no bearer token
export ALLOW_ANONYMOUS_DASHBOARD=1   # dashboard needs no login
export DATA_FILE="$(pwd)/presence.json"
.venv/bin/python observatory_presence.py
```

**b) Bearer token required — for working on the agent endpoints**
```bash
export SECRET_TOKEN="devtoken"
unset ALLOW_OPEN_API
export ALLOW_ANONYMOUS_DASHBOARD=1
export DATA_FILE="$(pwd)/presence.json"
.venv/bin/python observatory_presence.py
```
The Windows agent is not needed to exercise the API — curl does the same thing:
```bash
curl -sS -X POST http://localhost:5000/start \
  -H "Authorization: Bearer devtoken" -d "user=alice&target=saturn"
curl -sS -X POST http://localhost:5000/heartbeat \
  -H "Authorization: Bearer devtoken" \
  -H "Content-Type: application/json" -d '{"user":"alice"}'
curl -sS -X POST http://localhost:5000/release \
  -H "Authorization: Bearer devtoken"
```
`/start` answers **409** when a session is already running — including one left over in `presence.json` from an earlier run. Either `/release` it first, add `-d force=true`, or delete `presence.json`.

**c) Against the real LDAP directory — for working on the login**

Copy the connection values from `ost_inventory/.env`; both projects talk to the same directory.
```bash
export SECRET_TOKEN="devtoken"
export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SESSION_COOKIE_SECURE=0          # only because this is plain HTTP
export LDAP_SERVER_URI="ldaps://ldap.example.org"
export LDAP_USER_SEARCH_BASE="ou=people,dc=example,dc=org"
export LDAP_ALLOWED_GROUP_DNS="cn=observatory,ou=groups,dc=example,dc=org"
unset ALLOW_ANONYMOUS_DASHBOARD
export DATA_FILE="$(pwd)/presence.json"
.venv/bin/python observatory_presence.py
```
`http://localhost:5000/` now redirects to the login form. Worth checking before rolling anything out: an account in **none** of the configured groups must be rejected. See [Dashboard login (LDAP)](#dashboard-login-ldap) for the full variable list and a troubleshooting table.

> `ALLOW_OPEN_API` and `ALLOW_ANONYMOUS_DASHBOARD` exist for local development only. `SESSION_COOKIE_SECURE=0` is needed here solely because the dev server speaks plain HTTP. **Never set any of the three in production** — the app has no way to detect the mistake for you.

### 3) Check it works

Open `http://localhost:5000`. Locally, Flask serves the static assets under `/static/` and the dashboard polls `/status` every ~15 s.

`BASE_PATH` is empty in development, so URLs have no prefix. In production the app is mounted under `/ost_status` and Apache serves static files and camera media directly — keep that difference in mind when touching URLs in templates or `static/js/app.js`.

### 4) Run the tests

```bash
.venv/bin/python -m pytest tests/ -v
```
No LDAP server is required: `tests/conftest.py` substitutes a fake directory, so the login code path is exercised end to end against it.

### Optional: run it the way production does

To reproduce the Gunicorn setup (single worker, as required by the file-backed state and the in-process cleaner thread):
```bash
export ALLOW_OPEN_API=1 ALLOW_ANONYMOUS_DASHBOARD=1
export DATA_FILE="$(pwd)/presence.json"
.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5000 'observatory_presence:app'
```

### Optional: webcam images

```bash
mkdir -p ./camera_media
export CAMERA_MEDIA_DIR="$(pwd)/camera_media"
# copy or symlink outdoor_current.jpg, indoor_current.jpg, *.webm into camera_media/
```
Flask then serves them under `/media/cameras/...`; in production Apache serves `/ost_status/media/cameras/...` instead.

### Optional: the Windows agent

`autostart_client_prompt.ps1`, `deploy/windows/host_status_agent.ps1` and `deploy/windows/telescope_agent.ps1` are written for the observatory's Windows PC (Task Scheduler, ASCOM) and are not meant to run on the development machine. Use the curl calls above, or `deploy/tests/host_status_test.sh`, to produce the same requests. See [Windows client](#windows-client-powershell-task-scheduler) for the real thing.

---
## Production deployment (Debian, Apache reverse proxy to Gunicorn over UNIX socket)

Architecture:
- Apache serves HTTPS and reverse proxies to Gunicorn via a UNIX socket (`/run/observatory_presence/gunicorn.sock`).
- Browser access (GET/HEAD) is limited to campus / VPN prefixes; on top of that the app requires an LDAP login. POSTs are limited to the observatory client host(s).
- Gunicorn runs the Flask app with **one worker** (required while using file-backed state and the in-process cleaner thread).
- Humans authenticate against the campus LDAP directory (login form, session cookie). Access can be restricted to one or more LDAP groups.
- The app uses a shared-secret token to protect mutating endpoints (`/start`, `/heartbeat`, `/release`). The token is expected in the `Authorization: Bearer <TOKEN>` header. Agents never use LDAP.
- Application state persists to `presence.json` (default path configurable).

### 1) Prerequisites
- Debian/Ubuntu with Apache:
  ```bash
  sudo apt update
  sudo apt install -y apache2 apache2-utils python3 python3-pip python3-venv fail2ban
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
sudo pip3 install -r /mnt/data/observatory_presence/requirements.txt
```
Option B (recommended: venv):
```bash
sudo -u ost-status python3 -m venv /mnt/data/observatory_presence/.venv
sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install --upgrade pip
sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install -r /mnt/data/observatory_presence/requirements.txt
# Optional J2000 conversion on the server:
# sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install -r /mnt/data/observatory_presence/requirements-astropy.txt
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
# SESSION_SECRET=<long_random_string>
# DATA_FILE=/var/lib/observatory_presence/presence.json
# HEARTBEAT_TIMEOUT=90
# BASE_PATH=/ost_status
# TRUST_PROXY_HEADERS=1
# ... plus the LDAP_* block, see "Dashboard login (LDAP)" below
```
Generate the two secrets:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```
`SESSION_SECRET` signs the login cookie — changing it logs everyone out, so generate it once and keep it. The env file holds the LDAP service password, so keep it owned by the service user and `chmod 600`.

**The app refuses to start** without `SECRET_TOKEN`, without `SESSION_SECRET`, without `LDAP_SERVER_URI`/`LDAP_USER_SEARCH_BASE`, or with a plain `ldap://` URI and STARTTLS disabled. Check `journalctl -u observatory_presence` if the service does not come up.
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
# Update ServerName, certificate paths, campus/VPN prefixes, and the POST allowlist host.
sudo a2ensite observatory_presence
sudo systemctl reload apache2
```

Dashboard users come from LDAP — there is no htpasswd file any more. If you are upgrading from the Basic Auth setup, remove the obsolete one after the LDAP login works:
```bash
sudo rm /etc/apache2/ost_status.htpasswd
```

> **Do not put Basic Auth in front of the app.** The login lives in the application; an additional Apache `AuthType Basic` would ask users for a password twice.

Static files, media, access control, and reverse proxy (see `deploy/apache/observatory_presence.conf` for the full vhost). Replace the documentation addresses (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `192.0.2.10`) with your campus / VPN prefixes and observatory client host:
```apache
# Serve static assets directly via Apache.
# CSS/fonts are public so /ost_status/datenschutz can load without campus access.
# JS stays campus / VPN only via the <Directory> block below.
Alias /ost_status/static /mnt/data/observatory_presence/static

<Directory /mnt/data/observatory_presence/static>
        Options -Indexes
        Require all granted
</Directory>

<Directory /mnt/data/observatory_presence/static/js>
        Options -Indexes

        <RequireAny>
                Require ip 192.0.2.0/24
                Require ip 198.51.100.0/24
                Require ip 203.0.113.0/24
        </RequireAny>
</Directory>

# Do not proxy static requests
ProxyPass /ost_status/static !

# Webcam stills and videos (uploaded from the observatory)
Alias /ost_status/media/cameras /var/lib/observatory_cameras

<Directory /var/lib/observatory_cameras>
        Options -Indexes

        <RequireAny>
                Require ip 192.0.2.0/24
                Require ip 198.51.100.0/24
                Require ip 203.0.113.0/24
        </RequireAny>

        <FilesMatch "\.(?i:jpg|jpeg)$">
                Header set Cache-Control "no-cache"
        </FilesMatch>

        <FilesMatch "\.(?i:webm)$">
                Header set Cache-Control "max-age=3600"
        </FilesMatch>
</Directory>

ProxyPass /ost_status/media !

# GET/HEAD: campus / VPN (the app then requires an LDAP login)
# POST: observatory status client host only
<Location /ost_status>
        <Limit GET HEAD>
                <RequireAny>
                        Require ip 192.0.2.0/24
                        Require ip 198.51.100.0/24
                        Require ip 203.0.113.0/24
                </RequireAny>
        </Limit>

        <Limit POST>
                Require ip 192.0.2.10
        </Limit>
</Location>

# The login form POSTs from a browser, so it must not fall under the
# agent-only <Limit POST> above. This block must come AFTER <Location
# /ost_status> -- Apache applies Location sections in file order, and the
# later, more specific one wins.
<Location /ost_status/login>
        <RequireAny>
                Require ip 192.0.2.0/24
                Require ip 198.51.100.0/24
                Require ip 203.0.113.0/24
        </RequireAny>
</Location>

<Location /ost_status/logout>
        <RequireAny>
                Require ip 192.0.2.0/24
                Require ip 198.51.100.0/24
                Require ip 203.0.113.0/24
        </RequireAny>
</Location>

# Public camera privacy notice (QR code / posted sheet) — no campus IP, no login
<LocationMatch "^/ost_status/datenschutz/?$">
        <Limit GET HEAD>
                Require all granted
        </Limit>
        <LimitExcept GET HEAD>
                Require all denied
        </LimitExcept>
</LocationMatch>

# Privacy notice for the dashboard — must be readable before signing in
<LocationMatch "^/ost_status/privacy/?$">
        <Limit GET HEAD>
                Require all granted
        </Limit>
        <LimitExcept GET HEAD>
                Require all denied
        </LimitExcept>
</LocationMatch>

<Location /ost_status/static/css>
        Require all granted
</Location>

<Location /ost_status/static/fonts>
        Require all granted
</Location>

# Proxy to Gunicorn over UNIX socket.
# The hostname after the "|" is a dummy (never resolved), but mod_proxy uses it
# to identify the worker -- so it must be unique per socket-backed app.
ProxyPass        /ost_status unix:/run/observatory_presence/gunicorn.sock|http://ost-status.invalid/
ProxyPassReverse /ost_status http://ost-status.invalid/

<Location /ost_status/status>
        <LimitExcept GET>
                Require all denied
        </LimitExcept>
</Location>
```

Notes:
- Ensure your app is started with `BASE_PATH=/ost_status` so generated static URLs are `/ost_status/static/...`.
- Locally (without Apache), `BASE_PATH` can be empty; Flask will serve static files under `/static/`.
- Keep the campus / VPN prefixes in sync across the media, JS, and `/ost_status` `Location` blocks.
- Keep the `<Directory>` paths in sync with the `Alias` targets. A `<Directory>` block whose path no longer matches the aliased directory is silently inactive, which can quietly drop the campus / VPN restriction on `static/js`.
- If other apps on the same server are also proxied to UNIX sockets, give every `ProxyPass` a **unique** dummy hostname (e.g. `http://ost-status.invalid/`, `http://ost-inventory.invalid/`). Two UDS backends sharing `http://localhost/` collide on a single mod_proxy worker, and requests are routed to whichever backend was declared first in the configuration. The usual symptom is the *other* app's 404 page appearing under `/ost_status` while `systemctl status observatory_presence` still looks healthy.
- POSTs (session start/heartbeat/release, host/telescope status) must come from the observatory client host listed in `<Limit POST>`. The Flask app still requires `Authorization: Bearer`.
- `GET /ost_status/datenschutz` is public (no campus IP, no login) so it can be linked from a QR code on the posted camera notice. Camera images and the dashboard stay restricted.
- `GET /ost_status/privacy` is public as well: a privacy notice has to be readable *before* signing in, and it must stay reachable if the campus / VPN restriction is ever removed.

### 6b) Webcam media directory
Create the upload target and point your existing camera upload scripts at it:
```bash
sudo bash /mnt/data/observatory_presence/deploy/scripts/setup_camera_media_dir.sh <upload-user>
```
Expected filenames (same as the legacy livefeed page):
- `outdoor_current.jpg`, `indoor_current.jpg`
- `outdoor_video.webm`, `yesterday_outdoor_video.webm`

URLs on the dashboard (under `/ost_status`):
- `https://<host>/ost_status/media/cameras/outdoor_current.jpg`
- etc.

If you migrate from the old camera page, copy or symlink existing files into `/var/lib/observatory_cameras` and retire the old vhost path (optional redirect to `/ost_status/`).

### 7) Fail2ban (optional, recommended)
Blocks repeated unauthorized attempts: 401s on the agent POST endpoints, plus failed dashboard logins (401) and rate-limit hits (429) on `/ost_status/login`.
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

# Optionally also exercise the LDAP login end to end:
BASE_URL=https://observatory.example.org/ost_status TOKEN=<your_token> \
  LOGIN_USER=<your_uid> LOGIN_PASSWORD=<your_password> \
  bash /mnt/data/observatory_presence/deploy/tests/http_tests.sh
```
Without `LOGIN_USER`/`LOGIN_PASSWORD` the script asserts that `/status` answers **401** — i.e. that the dashboard really is protected.

Manual curl (browser endpoints need a session cookie; POST only succeeds from the observatory client host):
```bash
# Log in and keep the session cookie
CSRF=$(curl -sSL -c /tmp/jar https://observatory.example.org/ost_status/login \
  | grep -o 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
curl -sS -b /tmp/jar -c /tmp/jar -o /dev/null -w '%{http_code}\n' \
  --data-urlencode "csrf_token=$CSRF" \
  --data-urlencode "username=<your_uid>" \
  --data-urlencode "password=<your_password>" \
  https://observatory.example.org/ost_status/login   # expect 302

curl -sSL -b /tmp/jar https://observatory.example.org/ost_status/status | jq .
curl -sS -X POST https://observatory.example.org/ost_status/start \
  -H "Authorization: Bearer <your_token>" \
  -d "user=alice&target=saturn&planned_hours=2.5"
curl -sS -X POST https://observatory.example.org/ost_status/heartbeat \
  -H "Authorization: Bearer <your_token>" \
  -H "Content-Type: application/json" \
  -d '{"user":"alice"}'
curl -sS -X POST https://observatory.example.org/ost_status/release \
  -H "Authorization: Bearer <your_token>"
```

---
## Windows client (PowerShell + Task Scheduler)

Dashboard UI:
- Mobile‑first, English-only, auto-refreshes every ~15 seconds via `/status` polling.
- **Live cameras** at the top (outdoor/indoor JPGs load immediately; images refresh every ~45s).
- **Outdoor videos** (today / yesterday WebM, manual play).
- Sections: Current Session, Host Status, Telescope Status, **Session log** (completed sessions).
- Manual connect form is removed; sessions start via the client.

Script: `autostart_client_prompt.ps1`
- The script shows a small GUI to collect the observer name, optional target/note, and an optional planned window at logon, then keeps sending heartbeats until disconnect.
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
 - Planned window input (either/or):
   - Planned (hours): decimal hours (e.g., `1.5`) → server computes planned end
   - End time (HH:mm): local time today; if already passed, uses next day → server stores planned end
   - The dashboard shows “Planned end” when available.

Host status agent (optional, recommended):
- Script: `deploy/windows/host_status_agent.ps1`
- Purpose: sends host metrics every 60s (uptime, CPU%, RAM%, C: free %, OS version) to `${OBS_PRESENCE_SERVER}/host_status` with `${OBS_PRESENCE_TOKEN}`.
- Create a Scheduled Task (runs at startup, repeats every 1 minute, hidden window, whether user is logged on or not):
  - Program/script: `powershell.exe`
  - Arguments:
    ```
    -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "C:\observatory_presence\deploy\windows\host_status_agent.ps1"
    ```
  - Set environment variables for the run account (or inline):
    - Persistent:
      ```powershell
      setx OBS_PRESENCE_SERVER "https://observatory.example.org/ost_status"
      setx OBS_PRESENCE_TOKEN  "<YOUR_LONG_RANDOM_TOKEN>"
      ```
    - Inline (per task, no persistence):
      ```
      -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -Command "$env:OBS_PRESENCE_SERVER='https://observatory.example.org/ost_status'; $env:OBS_PRESENCE_TOKEN='<YOUR_LONG_RANDOM_TOKEN>'; & 'C:\observatory_presence\deploy\windows\host_status_agent.ps1'"
      ```

Telescope agent (Windows + ASCOM, optional):
- Script: `deploy/windows/telescope_agent.ps1`
- Purpose: reads RA/Dec from ASCOM telescope (`ASCOM.tenmicron_mount.Telescope`) and posts to `${OBS_PRESENCE_SERVER}/telescope_status` with `${OBS_PRESENCE_TOKEN}`.
- Behavior:
  - Every 10 minutes: try to connect if not connected (telescope may be powered off)
  - While connected: every 5 minutes read RA/Dec (JNow) and send only on change; at least every 20 minutes send a heartbeat
- Prerequisites on Windows:
  - Install ASCOM Platform
  - Install TenMicron driver (`ASCOM.tenmicron_mount.Telescope`)
- Scheduled Task (startup, hidden, repeat):
  - Program/script: `powershell.exe`
  - Arguments:
    ```
    -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "C:\observatory_presence\deploy\windows\telescope_agent.ps1"
    ```
  - Env vars (user context):
    ```powershell
    setx OBS_PRESENCE_SERVER "https://observatory.example.org/ost_status"
    setx OBS_PRESENCE_TOKEN  "<YOUR_LONG_RANDOM_TOKEN>"
    ```
  - Optional: override driver id
    ```
    -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "C:\observatory_presence\deploy\windows\telescope_agent.ps1" -DriverId "ASCOM.tenmicron_mount.Telescope"
    ```
- Server-side optional (J2000 conversion):
  - To convert JNow → J2000 on the server, install Astropy:
    ```bash
    sudo -u ost-status /mnt/data/observatory_presence/.venv/bin/pip install astropy
    ```
  - Without Astropy, values are stored as sent and marked via `frame`.

Manual telescope test (curl):
```bash
curl -sS -X POST https://observatory.example.org/ost_status/telescope_status \
  -H "Authorization: Bearer <your_token>" \
  -H "Content-Type: application/json" \
  -d '{"hostId":"OMS-PC","ts":"2025-01-01T00:00:00Z","raHours":12.345,"decDeg":-23.45,"frame":"JNow","tracking":true,"slewing":false}'
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
   - Arguments: `-ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\observatory_presence\autostart_client_prompt.ps1"`
   - Alternative (pass token inline without persistent env var):
     - Arguments:
       ```
       -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -Command "$env:OBS_PRESENCE_SERVER='https://observatory.example.org/ost_status'; $env:OBS_PRESENCE_TOKEN='YOUR_LONG_RANDOM_TOKEN'; & 'C:\observatory_presence\autostart_client_prompt.ps1'"
       ```
   - Condition: enable “Run only when user is logged on”

Troubleshooting execution policy:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# or unblock file
Unblock-File -Path "C:\observatory_presence\autostart_client_prompt.ps1"
```

---
## Access control (three layers)

| Layer | Who | Mechanism |
|-------|-----|-----------|
| Network | Browsers (GET/HEAD) | Apache `Require ip` — campus / VPN prefixes only |
| Browser | Humans | Application login against campus LDAP, session cookie, optionally restricted to LDAP groups |
| Agents | Observatory Windows scripts (POST) | Apache `Require ip` (observatory host) **and** `Authorization: Bearer` + `SECRET_TOKEN` |
| Public | Privacy notices | `GET /ost_status/datenschutz` and `GET /ost_status/privacy` (and their CSS/fonts) — no IP allowlist, no login |

The LDAP login and the bearer token are **not** interchangeable: agents have no directory account and never log in, humans never use the token. Camera media and dashboard JS remain campus / VPN only. The privacy page is intentionally reachable from the public internet so a QR code on the posted information sheet can open it.

Public (no login): `/ost_status/datenschutz`, `/ost_status/privacy`, `/ost_status/health`, `/ost_status/login`, and the CSS/font assets.
Login required: `/ost_status/` (the dashboard), `/ost_status/status`, `/ost_status/logbook`, `/ost_status/media/cameras/*`.
Token required (unchanged): `POST /start`, `/heartbeat`, `/release`, `/host_status`, `/telescope_status`.

---
## Dashboard login (LDAP)

Humans authenticate against the campus directory with the same credentials they use for ost_inventory; the connection settings can be copied from that project's `.env`. On success the app stores a signed session cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, scoped to `BASE_PATH`) with an **absolute** lifetime — reloading the page does not extend it.

### Which accounts may log in

`LDAP_ALLOWED_GROUP_DNS` holds the groups whose members get access. A DN contains commas, so **several DNs are separated by `;`**:

```
LDAP_ALLOWED_GROUP_DNS=cn=observatory,ou=groups,dc=example,dc=org;cn=astro-staff,ou=groups,dc=example,dc=org
```

Membership is accepted through any of three directory schemas, so this works whether the campus directory uses posix groups, `groupOfNames`, or a `memberOf` overlay:

1. the user entry's `memberOf` contains the group DN,
2. the group's `member` contains the user DN (`groupOfNames`),
3. the group's `memberUid` contains the uid (`posixGroup`).

> **Leaving `LDAP_ALLOWED_GROUP_DNS` empty means every valid directory account can open the dashboard.** The app logs a warning at startup in that case.

### Transport security

`ldaps://` is preferred. A plain `ldap://` URI without `LDAP_START_TLS=1` is **refused at startup**, because user passwords would otherwise cross the network in clear text. Certificates are always verified; point `LDAP_TLS_CACERT` at a CA file if the directory certificate is not signed by a system-trusted CA. If STARTTLS fails at runtime the app aborts the bind rather than falling back to plaintext.

### Brute-force protection

Failed logins are counted per username+IP: `LOGIN_MAX_ATTEMPTS` (default 5) within `LOGIN_LOCKOUT_SECONDS` (default 300) trigger a temporary lockout answered with HTTP 429. Failed logins answer 401, so fail2ban can act on both (see the filter in `deploy/fail2ban/`). Set `TRUST_PROXY_HEADERS=1` behind Apache, otherwise every request appears to come from the proxy and the counter lumps all users together.

### Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Service does not start | Missing `SESSION_SECRET`, `LDAP_SERVER_URI` or `LDAP_USER_SEARCH_BASE`, or `ldap://` without STARTTLS. The exit message says which. |
| "The directory service is currently unavailable" | Directory unreachable, TLS handshake failed, or the service bind (`LDAP_BIND_DN`) was rejected. See `journalctl -u observatory_presence`. |
| Login fails for everyone | Wrong `LDAP_USER_SEARCH_BASE` or `LDAP_USER_FILTER`. The journal logs "no directory entry for …". |
| Login fails only for some | Those accounts are in none of the `LDAP_ALLOWED_GROUP_DNS`. The journal logs "is in none of the allowed groups". |
| Everyone is logged out after a restart | `SESSION_SECRET` is not set to a stable value in the env file. |
| Login form always says "This form has expired" | Cookie not coming back — check `SESSION_COOKIE_SECURE` (needs HTTPS) and that `BASE_PATH` matches the Apache mount point. |

### Personal data processed by the login

The sign-in introduced personal data that the camera privacy notice does not cover, so the dashboard has its own notice at `/ost_status/privacy` (`templates/privacy.html`, English). It is public — a privacy notice has to be readable before signing in. What it documents:

| Data | Where | Retention |
|------|-------|-----------|
| Session cookie `ost_status_session` (uid, display name, group DNs) | Browser, signed | `SESSION_LIFETIME_HOURS` (12 h), or until sign-out |
| Directory attributes read at sign-in (`uid`, `cn`, `givenName`, `sn`, `mail`, group membership) | Not persisted; only uid, display name and groups go into the session | Duration of the session |
| Sign-in / failure / lockout / sign-out events with user name and client IP | systemd journal | 7 days |
| Apache access log (IP, time, URL, status, user agent, referrer) | `/var/log/apache2/access.log` | 7 days |
| Failed-attempt counter (user name + IP) | Process memory only | `LOGIN_LOCKOUT_SECONDS` (5 min), lost on restart |
| Observing session log (free-text name entered at the OMS, target, start/end, duration, reason) | `SESSION_LOG_FILE` | **No automatic deletion** |

The name in the observing session log is **not** the sign-in identity: it is free text typed into the dialog of `autostart_client_prompt.ps1` on the observatory PC and is never checked against the directory. The privacy notice states that giving it is voluntary and that a first name (or a group designation such as "Schulklasse" for guided observations) is enough.

If the journal retention on the server is ever changed away from 7 days, or a retention limit is introduced for the observing session log, update `templates/privacy.html` to match — the notice states concrete periods.

The camera notice at `templates/datenschutz.html` is deliberately untouched: it covers only the video surveillance and is linked from the posted information sheet. Visiting it sets no cookie.

---
## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_TOKEN` | *(required in production)* | Bearer token for POST endpoints |
| `ALLOW_OPEN_API` | unset | Set to `1` for local dev only (allows empty token) |
| `DATA_FILE` | `presence.json` | Persistent JSON state path |
| `SESSION_LOG_FILE` | `<dir of DATA_FILE>/session_log.json` | Append-only session history |
| `SESSION_LOG_DEFAULT_LIMIT` | `100` | Default rows returned by `GET /logbook` (max 500) |
| `BASE_PATH` | empty | URL prefix, e.g. `/ost_status` |
| `HEARTBEAT_TIMEOUT` | `90` | Auto-release session after missing heartbeats (seconds) |
| `HOST_STATUS_TTL_SEC` | `3600` | Remove stale host entries after this age |
| `TELESCOPE_STATUS_TTL_SEC` | `2400` | Remove stale telescope entries after this age |
| `CAMERA_MEDIA_DIR` | `/var/lib/observatory_cameras` | Local dev fallback for camera files |

Dashboard login:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SESSION_SECRET` | *(required in production)* | Signs the login cookie; keep it stable |
| `LDAP_SERVER_URI` | *(required in production)* | e.g. `ldaps://ldap.example.org` |
| `LDAP_START_TLS` | `1` | Require STARTTLS on `ldap://` (refused at startup without it) |
| `LDAP_TLS_CACERT` | empty | CA file for certificate verification |
| `LDAP_BIND_DN` | empty | Service account for the user lookup (empty = anonymous search) |
| `LDAP_BIND_PASSWORD` | empty | Password for `LDAP_BIND_DN` |
| `LDAP_USER_SEARCH_BASE` | *(required in production)* | e.g. `ou=people,dc=example,dc=org` |
| `LDAP_USER_FILTER` | `(uid=%(user)s)` | User search filter |
| `LDAP_ALLOWED_GROUP_DNS` | empty | Groups allowed to log in, **separated by `;`**. Empty = any valid account |
| `LDAP_CONNECT_TIMEOUT` | `5` | Seconds |
| `SESSION_LIFETIME_HOURS` | `12` | Absolute session lifetime (not extended by activity) |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins per user+IP before lockout |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration |
| `SESSION_COOKIE_SECURE` | `1` | Set to `0` **only** for local HTTP testing |
| `TRUST_PROXY_HEADERS` | unset | Set to `1` behind Apache so rate limiting sees the real client IP |
| `ALLOW_ANONYMOUS_DASHBOARD` | unset | Skips the login entirely — **local development only** |

Production: set `SECRET_TOKEN`, `SESSION_SECRET` and the `LDAP_*` block in `observatory_presence.env`, and **do not** set `ALLOW_OPEN_API` or `ALLOW_ANONYMOUS_DASHBOARD`.

---
## Session log

When a session ends (`/release`, heartbeat timeout, or `force` override), an entry is appended to `SESSION_LOG_FILE` with:

- `id` (UUID, for future edit/delete)
- `user`, `target`, `start`, `end`, `durationSec`, `plannedEnd`, `endReason` (`release` | `timeout` | `force`)

The dashboard loads `GET /logbook` (newest first, default last 100 entries). All entries are kept (no cap). Optional query: `GET /logbook?limit=50`.

---
## Security notes
- Always use HTTPS on Apache.
- Use a long random `SECRET_TOKEN` and rotate periodically; `OBS_PRESENCE_TOKEN` must be set on Windows clients (no script fallback).
- The app refuses to start without `SECRET_TOKEN` unless `ALLOW_OPEN_API=1` (dev only).
- Browser GET/HEAD is limited to campus / VPN prefixes **and** an LDAP login in the app.
- Keep `SESSION_SECRET` secret and stable; it signs the login cookie. The env file also holds `LDAP_BIND_PASSWORD`, so `chmod 600` it and keep it owned by the service user.
- Restrict access with `LDAP_ALLOWED_GROUP_DNS`; an empty value lets every directory account in.
- Do not add Apache Basic Auth in front of the app — the login is in the application, and users would be prompted twice.
- The login writes usernames and client IPs to the journal (systemd default retention applies).
- `GET /ost_status/datenschutz` is the public exception (QR code / posted camera notice). It must not expose live images or the dashboard.
- POST is limited to the observatory client host(s); do not allow campus browsers to POST.
- Keep campus / VPN prefixes identical in the media, JS, and `/ost_status` `Location` blocks.
- fail2ban can reduce brute-force attempts; for burst control add `mod_evasive` or app-level rate-limiting if needed.
- `GET /ost_status/health` needs no login, so uptime monitors work (still subject to the campus / VPN allowlist unless you add a separate `Location`).

---
## Files of interest
- `observatory_presence.py` (Flask app)
- `ldap_auth.py` (LDAP bind, TLS hardening, group membership)
- `templates/index.html` / `templates/login.html` (dashboard, login form)
- `templates/datenschutz.html` (public camera privacy notice, German — linked from the posted information sheet)
- `templates/privacy.html` (privacy notice for the dashboard itself: sign-in, session, session log)
- `autostart_client_prompt.ps1` (Windows client)
- `deploy/systemd/observatory_presence.service` (systemd unit)
- `deploy/systemd/observatory_presence.env.example` (environment variables)
- `deploy/apache/observatory_presence.conf` (Apache vhost)
- `deploy/fail2ban/filter.d/observatory_presence.conf` and `deploy/fail2ban/jail.d/observatory_presence.local`
- `deploy/scripts/setup_data_dir.sh` (creates data dir with permissions)
- `deploy/scripts/setup_camera_media_dir.sh` (creates webcam/video upload directory)
- `static/fonts/` (Inter WOFF2, self-hosted; see `LICENSE.txt`)
- `requirements.txt` / `requirements-astropy.txt` (Python dependencies)
- `presence.json.example` / `session_log.json.example` (templates; real files are gitignored)
- `deploy/tests/http_tests.sh` (basic E2E test)
- `deploy/apache/camera_redirect.conf.example` (legacy livefeed redirect)
- `tests/test_presence.py` / `tests/test_ldap_auth.py` (pytest unit tests)
- `deploy/windows/host_status_agent.ps1` (Windows host metrics agent)
- `deploy/windows/telescope_agent.ps1` (Windows ASCOM telescope agent)

---
## Local testing (dashboard)

Setting up the venv and the three run modes is covered in [Development setup (Linux)](#development-setup-linux). This section only lists the scripted checks.

Unit tests (no LDAP server required — `tests/conftest.py` fakes the directory):
```bash
.venv/bin/python -m pytest tests/ -v
```

Host status agent simulation:
```bash
BASE_URL=http://localhost:5000 TOKEN=devtoken \
  bash deploy/tests/host_status_test.sh
```

End-to-end HTTP checks against a running instance. Without `LOGIN_USER`/`LOGIN_PASSWORD` the script asserts that `/status` answers 401, i.e. that the dashboard is actually protected:
```bash
BASE_URL=http://localhost:5000 TOKEN=devtoken \
  bash deploy/tests/http_tests.sh
```

---
## Deployment checklist (after upgrades)

1. Install dependencies: `pip install -r requirements.txt` (and optionally `requirements-astropy.txt`). `ldap3` is new — the venv needs it or the service will not start.
2. Set `SECRET_TOKEN`, `SESSION_SECRET`, `TRUST_PROXY_HEADERS=1` and the `LDAP_*` block in `config/observatory_presence.env`; ensure `ALLOW_OPEN_API` and `ALLOW_ANONYMOUS_DASHBOARD` are **not** set. `chmod 600` the file (it holds `LDAP_BIND_PASSWORD`).
3. Copy/update `deploy/apache/observatory_presence.conf` (campus / VPN prefixes, observatory POST host) — the Basic Auth block is gone and `/ost_status/login` needs its own `Location` so browser logins are not blocked by the agent-only `<Limit POST>`. `sudo apachectl configtest`, restart the app, reload Apache.
4. Copy the updated fail2ban filter (it now also matches failed logins) and `systemctl restart fail2ban`.
5. Roll out updated `autostart_client_prompt.ps1`; verify `OBS_PRESENCE_TOKEN` on each client. The agents are unaffected by the login.
6. From campus / VPN: open `https://<host>/ost_status/` in a browser and log in; verify an account in none of the allowed groups is rejected. Then `BASE_URL=https://<host>/ost_status TOKEN=<token> LOGIN_USER=<uid> LOGIN_PASSWORD=<pw> bash deploy/tests/http_tests.sh`.
7. Verify `https://<host>/ost_status/privacy` opens without a login (it is linked from the sign-in page). If the journal retention or the observing-log retention differs from what `templates/privacy.html` states, correct the page.
8. Once the LDAP login works, delete the obsolete `/etc/apache2/ost_status.htpasswd`.
9. Optional: redirect legacy camera URL using `deploy/apache/camera_redirect.conf.example`.
