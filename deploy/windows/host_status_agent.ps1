# Sends Windows host status to the central server every 60s
# Reads OBS_PRESENCE_SERVER and OBS_PRESENCE_TOKEN from environment

# Hidden/minimized console
try {
  $Host.UI.RawUI.WindowTitle = "Observatory Presence Host Agent"
} catch {}

$Server = $env:OBS_PRESENCE_SERVER
if (-not $Server -or $Server -eq "") {
  Write-Host "OBS_PRESENCE_SERVER not set" -ForegroundColor Yellow
  exit 1
}
$Token = $env:OBS_PRESENCE_TOKEN
if (-not $Token -or $Token -eq "") {
  Write-Host "OBS_PRESENCE_TOKEN not set" -ForegroundColor Yellow
  exit 1
}
$Headers = @{ Authorization = "Bearer $Token" }

function Get-UptimeSec {
  $os = Get-CimInstance Win32_OperatingSystem
  $boot = $os.LastBootUpTime
  [int]([DateTime]::UtcNow - $boot.ToUniversalTime()).TotalSeconds
}

function Get-CpuPercent {
  try {
    (Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1).CounterSamples.CookedValue
  } catch { 0 }
}

function Get-MemPercent {
  $os = Get-CimInstance Win32_OperatingSystem
  $total = [double]$os.TotalVisibleMemorySize
  $free  = [double]$os.FreePhysicalMemory
  if ($total -gt 0) { [math]::Round((($total - $free) / $total) * 100, 1) } else { 0 }
}

function Get-DiskCFreePercent {
  $c = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
  if ($c.Size -gt 0) { [math]::Round(($c.FreeSpace / $c.Size) * 100, 1) } else { 0 }
}

$hostId = $env:COMPUTERNAME
$osVersion = [System.Environment]::OSVersion.VersionString

while ($true) {
  $payload = [pscustomobject]@{
    hostId       = $hostId
    ts           = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    uptimeSec    = Get-UptimeSec
    cpuPercent   = [double](Get-CpuPercent)
    memPercent   = [double](Get-MemPercent)
    diskCPercent = [double](Get-DiskCFreePercent)
    osVersion    = $osVersion
  } | ConvertTo-Json
  try {
    Invoke-RestMethod -Method Post -Uri "$Server/host_status" -Headers $Headers -ContentType "application/json" -Body $payload | Out-Null
  } catch {
    # swallow errors; retry next cycle
  }
  Start-Sleep -Seconds 60
}


