# Starts the clipping studio: PeakCut's server (hidden, restarted if it stops), the brain in its own window,
# and the dashboard in the browser.   start.cmd [-Mode loop|channel] [-NoBrain]
param([ValidateSet("loop", "channel")] [string]$Mode = "loop", [switch]$NoBrain)
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path "$root\run" | Out-Null
Remove-Item "$root\run\stopping" -ErrorAction SilentlyContinue

function Running($pidFile) {
    if (-not (Test-Path $pidFile)) { return $false }
    return [bool](Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue)
}

if (-not (Running "$root\run\server-runner.pid")) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\run-server.ps1")
    Write-Host "Starting PeakCut..."
}
$up = $false
foreach ($i in 1..60) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8765/healthz" | Out-Null; $up = $true; break } catch { Start-Sleep 1 }
}
if (-not $up) { Write-Host "PeakCut did not start. See run\server.log." -ForegroundColor Red; exit 1 }

if (-not $NoBrain -and -not (Running "$root\run\brain-runner.pid")) {
    Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "$root\scripts\run-brain.ps1", "-Mode", $Mode)
    Write-Host "The brain is starting in its own window."
}
Start-Process "http://127.0.0.1:8765/app"
Write-Host "PeakCut is running at http://127.0.0.1:8765/app"
