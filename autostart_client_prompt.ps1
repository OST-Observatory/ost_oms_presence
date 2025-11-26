# This script prompts for name and optional note at login, then starts heartbeats.
# Flow:
# 1. Task Scheduler starts this script at user logon.
# 2. Prompt asks "Who is observing?" and an optional note.
# 3. Registers the session with the Flask server.
# 4. Sends heartbeats until the RDP session ends.

# Configuration
# Central server URL (Apache → Gunicorn)
# Prefer environment variable OBS_PRESENCE_SERVER; fallback to default
$Server = $env:OBS_PRESENCE_SERVER
if (-not $Server -or $Server -eq "") {
    $Server = "https://observatory.example.org/ost_status"
}
# Secret token (prefer environment variable OBS_PRESENCE_TOKEN)
$Token = $env:OBS_PRESENCE_TOKEN
if (-not $Token -or $Token -eq "") {
    # Fallback placeholder; replace or set OBS_PRESENCE_TOKEN in the user env
    $Token = "CHANGE_ME_LONG_RANDOM"
}
$Headers = @{ Authorization = "Bearer $Token" }

# User prompt
$User = Read-Host "Please enter your name (observer)"
$Target = Read-Host "Optional: Target / note (e.g., object name)"

# Register session on the server
try {
    Invoke-RestMethod -Method Post -Uri "$Server/start" -Headers $Headers -Body @{user=$User; target=$Target}
    Write-Host "Session started for $User."
}
catch {
    Write-Host "Error starting session: $_"
    exit 1
}

# Heartbeat loop
while ($true) {
    try {
        $body = @{ user = $User } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri "$Server/heartbeat" -Headers $Headers -ContentType "application/json" -Body $body | Out-Null
    }
    catch {
        Write-Host "Heartbeat failed: $_"
    }
    Start-Sleep -Seconds 20
}

# ------------------------------------------------------------
# Task Scheduler Setup (Windows 11)
# ------------------------------------------------------------
# 1. Open "Task Scheduler".
# 2. Create a new task → Name e.g. "Observatory Presence Client".
# 3. Trigger: "At log on".
# 4. Action: "Start a program" → `powershell.exe`
#    - Arguments: `-ExecutionPolicy Bypass -File "C:\Path\to\autostart_client_prompt.ps1"`
# 5. Option: enable "Run only when user is logged on".
# 6. Test: After the next RDP logon a prompt for name/target appears.
#
# ------------------------------------------------------------
# Notes:
# - If you don't want a prompt (shared account), you can use `$env:USERNAME` automatically.
# - The prompt is useful when several people use the same Windows account.
# - The script runs as long as the RDP session is open. After disconnect Windows ends the process automatically → the presence server detects missing heartbeats and frees the session.
# - Server timeout (90s) ensures the session is released even on abrupt termination.

