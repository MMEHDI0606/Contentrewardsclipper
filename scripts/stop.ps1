# Stops the brain and PeakCut's server (and their restart loops).
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path "$root\run" | Out-Null
"stop" | Set-Content "$root\run\stopping"
foreach ($name in "brain-runner", "server-runner") {
    $f = "$root\run\$name.pid"
    if (Test-Path $f) {
        & taskkill /PID ([int](Get-Content $f)) /T /F 2>$null | Out-Null
        Remove-Item $f -ErrorAction SilentlyContinue
    }
}
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Write-Host "Stopped."
