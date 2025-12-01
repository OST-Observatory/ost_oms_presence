# This script prompts for name and optional note at login, then starts heartbeats.
# Flow:
# 1. Task Scheduler starts this script at user logon.
# 2. GUI asks "Who is observing?" and an optional note.
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

# Console UX: title + minimize/hint to avoid accidental close
try {
    $Host.UI.RawUI.WindowTitle = "Observatory Presence Client - Please do not close"
    Write-Host "Please do not close this window. You can minimize it. The client keeps your session active until you disconnect." -ForegroundColor Yellow
    $sig = @"
using System;
using System.Runtime.InteropServices;
public static class Win32ShowWindow {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
    Add-Type -TypeDefinition $sig -ErrorAction SilentlyContinue | Out-Null
    $hwnd = (Get-Process -Id $PID).MainWindowHandle
    if ($hwnd -ne [IntPtr]::Zero) { [Win32ShowWindow]::ShowWindow($hwnd, 6) } # 6 = SW_MINIMIZE
} catch { }

# GUI prompt (WPF)
Add-Type -AssemblyName PresentationFramework | Out-Null

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Observatory Presence" Height="310" Width="680" WindowStartupLocation="CenterScreen" ResizeMode="NoResize">
  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <TextBlock Text="Who is observing this session?" FontSize="16" FontWeight="Bold" Margin="0,0,0,8"/>
    <TextBlock Grid.Row="1" Text="This helps others see who is currently using the observatory and what target/activity is planned." TextWrapping="Wrap" Margin="0,0,0,12"/>

    <StackPanel Grid.Row="2" Orientation="Vertical">
      <StackPanel Orientation="Horizontal" Margin="0,0,0,6">
        <TextBlock Text="Name:" Width="110" VerticalAlignment="Center"/>
        <TextBox x:Name="NameBox" Width="440"/>
      </StackPanel>
      <StackPanel Orientation="Horizontal" Margin="0,0,0,6">
        <TextBlock Text="Target / note:" Width="110" VerticalAlignment="Center"/>
        <TextBox x:Name="TargetBox" Width="440"/>
      </StackPanel>
      <StackPanel Orientation="Horizontal" Margin="0,0,0,6">
        <TextBlock Text="Planned (hours):" Width="110" VerticalAlignment="Center"/>
        <TextBox x:Name="PlannedHoursBox" Width="140"/>
        <TextBlock Text="Optional. Estimated observing duration." Margin="8,0,0,0" VerticalAlignment="Center" Foreground="Gray" TextWrapping="Wrap" Width="360"/>
      </StackPanel>
      <StackPanel Orientation="Horizontal">
        <TextBlock Text="End time (HH:mm):" Width="110" VerticalAlignment="Center"/>
        <TextBox x:Name="EndTimeBox" Width="140"/>
        <TextBlock Text="Optional. If set, overrides planned hours." Margin="8,0,0,0" VerticalAlignment="Center" Foreground="Gray" TextWrapping="Wrap" Width="360"/>
      </StackPanel>
    </StackPanel>

    <TextBlock Grid.Row="3" Foreground="Gray" FontSize="12" TextWrapping="Wrap" Margin="0,10,0,10"
               Text="Tip: This window only appears at login. The client will keep the session alive with periodic heartbeats until you disconnect."/>

    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right">
      <Button x:Name="CancelBtn" Content="Cancel" Width="90" Margin="0,0,8,0"/>
      <Button x:Name="StartBtn" Content="Start" Width="90" IsDefault="True"/>
    </StackPanel>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader ([xml]$xaml)
$window = [Windows.Markup.XamlReader]::Load($reader)
$NameBox = $window.FindName("NameBox")
$TargetBox = $window.FindName("TargetBox")
$PlannedHoursBox = $window.FindName("PlannedHoursBox")
$EndTimeBox = $window.FindName("EndTimeBox")
$StartBtn = $window.FindName("StartBtn")
$CancelBtn = $window.FindName("CancelBtn")

$result = $null
$StartBtn.Add_Click({
    if ([string]::IsNullOrWhiteSpace($NameBox.Text)) {
        [System.Windows.MessageBox]::Show("Please enter your name.", "Observatory Presence", "OK", "Warning") | Out-Null
        return
    }
    $script:result = [PSCustomObject]@{
        User   = $NameBox.Text.Trim()
        Target = $TargetBox.Text.Trim()
        PlannedHours = $PlannedHoursBox.Text.Trim()
        EndTime      = $EndTimeBox.Text.Trim()
    }
    $window.Close()
})
$CancelBtn.Add_Click({
    $script:result = $null
    $window.Close()
})

[void]$window.ShowDialog()
if ($null -eq $result) {
    Write-Host "Cancelled by user."
    exit 0
}
$User = $result.User
$Target = $result.Target
$PlannedHoursText = $result.PlannedHours
$EndTimeText = $result.EndTime
$HasPlannedHours = $false
$PlannedHours = 0.0
if ($PlannedHoursText -and ($PlannedHoursText -match '^[0-9]+(\.[0-9]+)?$')) {
    $PlannedHours = [double]$PlannedHoursText
    if ($PlannedHours -lt 0) { $PlannedHours = 0 }
    if ($PlannedHours -gt 0) { $HasPlannedHours = $true }
}

# Parse end time (HH:mm) local -> ISO UTC
$HasEndTime = $false
$PlannedEndIso = $null
if ($EndTimeText -and ($EndTimeText -match '^\d{1,2}:\d{2}$')) {
    try {
        $parts = $EndTimeText.Split(':')
        $h = [int]$parts[0]; $m = [int]$parts[1]
        if ($h -ge 0 -and $h -lt 24 -and $m -ge 0 -and $m -lt 60) {
            $nowLocal = Get-Date
            $endLocal = Get-Date -Year $nowLocal.Year -Month $nowLocal.Month -Day $nowLocal.Day -Hour $h -Minute $m -Second 0
            if ($endLocal -lt $nowLocal) {
                $endLocal = $endLocal.AddDays(1)
            }
            $PlannedEndIso = $endLocal.ToUniversalTime().ToString("s") + "Z"
            $HasEndTime = $true
        }
    } catch { }
}

# Register session on the server
try {
    $body = @{ user=$User; target=$Target }
    if ($HasEndTime) {
        $body['planned_end'] = $PlannedEndIso
    } elseif ($HasPlannedHours) {
        $body['planned_hours'] = $PlannedHours
    }
    Invoke-RestMethod -Method Post -Uri "$Server/start" -Headers $Headers -Body $body
    Write-Host "Session started for $User."
}
catch {
    Write-Host "Error starting session: $_"
    # Only retry with force on HTTP 409 Conflict (session already occupied)
    $statusCode = $null
    try {
        if ($_.Exception -and $_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        } elseif ($_.Exception.StatusCode) {
            $statusCode = [int]$_.Exception.StatusCode
        }
    } catch { }
    if ($statusCode -ne 409) {
        exit 1
    }
    Write-Host "Retrying with force override due to 409 Conflict..."
    try {
        $body['force'] = $true
        Invoke-RestMethod -Method Post -Uri "$Server/start" -Headers $Headers -Body $body
        Write-Host "Session started for $User (forced)."
    } catch {
        Write-Host "Forced start failed: $_"
        exit 1
    }
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

