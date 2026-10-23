<#
.SYNOPSIS
  Runs the Stove Pilot API and UI on demand on this PC (Windows PowerShell 5.1).

.DESCRIPTION
  Starts stove-pilot-api, waits for /api/v1/health, starts stove-pilot-ui, prints the UI URL
  and keeps both running until Ctrl+C. Both processes are stopped on exit.
  The apps read their own settings from .env in the working directory (this script loads nothing).
  The project virtualenv is resolved relative to this script: <project>\.venv.

.PARAMETER WorkingDirectory
  Directory the apps run in (where .env is looked up). Default: the project root.

.PARAMETER NoBrowser
  Do not open the UI in the default browser.

.PARAMETER SmokeTest
  Start both apps, check that the API and the UI answer, stop them and exit (exit code 0 on success).

.PARAMETER ApiPort
  API port used for the health check (default: STOVE_PILOT_API_PORT or 8710).

.PARAMETER UiPort
  UI port used for the URL and the readiness check (default: STOVE_PILOT_UI_PORT or 8711).

.PARAMETER HealthTimeoutSec
  Seconds to wait for the API and the UI to answer before giving up (default 60).

.PARAMETER Dev
  Set STOVE_PILOT_DEV for the two apps so each restarts when a watched file is saved.
  The variable is restored when this script exits. Do not use this for the scheduled task.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\run.ps1
#>
[CmdletBinding()]
param(
  [string]$WorkingDirectory = "",
  [switch]$NoBrowser,
  [switch]$SmokeTest,
  [int]$ApiPort = 0,
  [int]$UiPort = 0,
  [int]$HealthTimeoutSec = 60,
  [switch]$Dev
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ($WorkingDirectory -eq "") { $WorkingDirectory = $projectRoot }
if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
  throw "Working directory not found: $WorkingDirectory"
}
$WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path

if ($ApiPort -le 0) { $ApiPort = if ($env:STOVE_PILOT_API_PORT) { [int]$env:STOVE_PILOT_API_PORT } else { 8710 } }
if ($UiPort -le 0) { $UiPort = if ($env:STOVE_PILOT_UI_PORT) { [int]$env:STOVE_PILOT_UI_PORT } else { 8711 } }

$scripts = Join-Path $projectRoot ".venv\Scripts"
$apiExe = Join-Path $scripts "stove-pilot-api.exe"
$uiExe = Join-Path $scripts "stove-pilot-ui.exe"
foreach ($exe in @($apiExe, $uiExe)) {
  if (-not (Test-Path -LiteralPath $exe)) {
    throw "Not found: $exe. Create the venv and run 'pip install .' first (see README, Setup)."
  }
}

function Test-PortFree {
  param([int]$Port)
  $busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
  return ($null -eq $busy)
}

function Stop-Tree {
  param($Process)
  if ($null -eq $Process) { return }
  # Reason: the pip launcher spawns the real python process as a child, so kill the whole tree.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
  $ErrorActionPreference = $previous
}

function Wait-Until {
  param([scriptblock]$Probe, [string]$What, $Process, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) {
      throw "$What stopped early (exit code $($Process.ExitCode)). Check the messages above and your .env."
    }
    if (& $Probe) { return }
    Start-Sleep -Milliseconds 500
  }
  throw "$What did not answer within $TimeoutSec s."
}

function Test-ApiHealth {
  param([int]$Port)
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/v1/health" -UseBasicParsing -TimeoutSec 3
    return ($r.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Test-UiAnswers {
  param([int]$Port)
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5
    return ($r.StatusCode -eq 200)
  } catch {
    return $false
  }
}

foreach ($port in @($ApiPort, $UiPort)) {
  if (-not (Test-PortFree -Port $port)) {
    throw "Port $port is already in use. Stop the other Stove Pilot instance first."
  }
}

$api = $null
$ui = $null
$savedDev = $env:STOVE_PILOT_DEV
try {
  if ($Dev) {
    $env:STOVE_PILOT_DEV = "true"
    Write-Host "Dev reload is on. Saving a source file restarts the app that uses it. Refresh the browser once afterwards."
  }
  Write-Host "Starting the API (working directory: $WorkingDirectory)"
  $api = Start-Process -FilePath $apiExe -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru
  $null = $api.Handle
  Wait-Until -Probe { Test-ApiHealth -Port $ApiPort } -What "The API" -Process $api -TimeoutSec $HealthTimeoutSec
  Write-Host "API healthy on port $ApiPort"

  Write-Host "Starting the UI"
  $ui = Start-Process -FilePath $uiExe -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru
  $null = $ui.Handle
  Wait-Until -Probe { Test-UiAnswers -Port $UiPort } -What "The UI" -Process $ui -TimeoutSec $HealthTimeoutSec

  $url = "http://localhost:$UiPort/"
  Write-Host ""
  Write-Host "Stove Pilot is running: $url"
  Write-Host "The UI has no login: keep it on the LAN or VPN only. Press Ctrl+C to stop both apps."
  if ($SmokeTest) {
    Write-Host "Smoke test OK."
  } else {
    if (-not $NoBrowser) { Start-Process $url }
    while (-not $api.HasExited -and -not $ui.HasExited) { Start-Sleep -Seconds 1 }
    if ($api.HasExited) { Write-Warning "The API stopped (exit code $($api.ExitCode))." }
    if ($ui.HasExited) { Write-Warning "The UI stopped (exit code $($ui.ExitCode))." }
  }
} finally {
  Write-Host "Stopping Stove Pilot"
  Stop-Tree -Process $ui
  Stop-Tree -Process $api
  if ($null -eq $savedDev) { Remove-Item Env:STOVE_PILOT_DEV -ErrorAction SilentlyContinue }
  else { $env:STOVE_PILOT_DEV = $savedDev }
}
