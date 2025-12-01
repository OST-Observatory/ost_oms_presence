# Telescope status agent for Windows (ASCOM)
# - Every 10 minutes tries to connect if not connected
# - If connected, every 5 minutes sends RA/Dec (JNow) and flags; sends at least every 20 minutes as heartbeat
# Requirements: ASCOM Platform, TenMicron driver ("ASCOM.tenmicron_mount.Telescope")

param(
  [string]$DriverId = "ASCOM.tenmicron_mount.Telescope"
)

$ErrorActionPreference = "Stop"

try { $Host.UI.RawUI.WindowTitle = "Observatory Presence Telescope Agent" } catch {}

function Send-TelescopeStatus {
  param($Server, $Token, $HostId, $RaHours, $DecDeg, $Tracking, $Slewing)
  $headers = @{ Authorization = "Bearer $Token" }
  $payload = [pscustomobject]@{
    hostId   = $HostId
    ts       = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    raHours  = [double]$RaHours
    decDeg   = [double]$DecDeg
    frame    = "JNow"
    tracking = $Tracking
    slewing  = $Slewing
  } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "$Server/telescope_status" -Headers $headers -ContentType "application/json" -Body $payload | Out-Null
}

function NearlyEqual {
  param([double]$a, [double]$b, [double]$eps)
  if ($a -eq $null -or $b -eq $null) { return $false }
  return ([math]::Abs($a - $b) -le $eps)
}

$Server = $env:OBS_PRESENCE_SERVER
if (-not $Server) { Write-Host "OBS_PRESENCE_SERVER not set" -ForegroundColor Yellow; exit 1 }
$Token  = $env:OBS_PRESENCE_TOKEN
if (-not $Token)  { Write-Host "OBS_PRESENCE_TOKEN not set"  -ForegroundColor Yellow; exit 1 }
$HostId = $env:COMPUTERNAME

# Try .NET DriverAccess first, then COM ProgID fallback
$useCom = $false
try {
  Add-Type -AssemblyName ASCOM.DriverAccess -ErrorAction Stop
} catch {
  $useCom = $true
  Write-Host "ASCOM.DriverAccess not found. Falling back to COM ProgID ($DriverId)." -ForegroundColor Yellow
}

$scope = $null
$lastSent = Get-Date "2000-01-01"
$lastRa = $null
$lastDec = $null

while ($true) {
  try {
    # Try connect every 10 minutes if not connected
    if (-not $scope) {
      if (-not $useCom) {
        $scope = New-Object ASCOM.DriverAccess.Telescope($DriverId)
      } else {
        $scope = New-Object -ComObject $DriverId
      }
    }
    if (-not $scope.Connected) {
      $scope.Connected = $true
    }
  } catch {
    # Not available, sleep 10 minutes
    Start-Sleep -Seconds 600
    continue
  }

  $now = Get-Date
  $send = $false
  $ra = $null; $dec = $null; $tracking = $null; $slewing = $null
  try {
    $ra = [double]$scope.RightAscension
    $dec = [double]$scope.Declination
    $tracking = $scope.Tracking
    $slewing = $scope.Slewing
    # Send on change (RA ~0.001h, Dec ~0.01deg) or at least every 20 minutes
    if (-not (NearlyEqual $ra $lastRa 0.001) -or -not (NearlyEqual $dec $lastDec 0.01)) { $send = $true }
    if (-not $send -and ($now - $lastSent).TotalMinutes -ge 20) { $send = $true }
  } catch {
    # If read fails, consider disconnecting and retry later
    $send = $false
  }

  if ($send -and $ra -ne $null -and $dec -ne $null) {
    try {
      Send-TelescopeStatus -Server $Server -Token $Token -HostId $HostId -RaHours $ra -DecDeg $dec -Tracking $tracking -Slewing $slewing
      $lastSent = Get-Date
      $lastRa = $ra; $lastDec = $dec
    } catch {
      # ignore send errors
    }
  }

  # If telescope disconnected meanwhile, clean up and retry after 10 minutes
  if (-not $scope.Connected) {
    try {
      if ($scope) {
        if ($useCom) {
          # Release COM reference explicitly
          try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($scope) } catch {}
        } elseif ($scope -is [System.IDisposable]) {
          $scope.Dispose()
        }
      }
    } catch {} finally {
      $scope = $null
      try { [GC]::Collect(); [GC]::WaitForPendingFinalizers() } catch {}
    }
    $scope = $null
    Start-Sleep -Seconds 600
    continue
  }

  # Poll cadence while connected: 5 minutes
  Start-Sleep -Seconds 300
}


