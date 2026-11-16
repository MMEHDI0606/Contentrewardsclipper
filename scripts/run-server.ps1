# Keeps PeakCut's server running: starts it, and starts it again if it stops. Also keeps this computer awake
# while it runs (the screen may still turn off). Started hidden by start.ps1; stop.ps1 ends it.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\run" | Out-Null
$PID | Set-Content "$root\run\server-runner.pid"

Add-Type -Namespace PeakCut -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[PeakCut.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null     # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

# Settings for this install (the same names work in .env; these are only defaults)
foreach ($kv in @{ PEAKCUT_UPDATE_CHECK = "0"; PYTHONUTF8 = "1"; PEAKCUT_AI_PROVIDER = "claude_session"; PEAKCUT_AI_MODELS = "sonnet" }.GetEnumerator()) {
    if (-not [Environment]::GetEnvironmentVariable($kv.Key)) { [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value) }
}

$log = "$root\run\server.log"
while ($true) {
    "[$(Get-Date -Format s)] starting PeakCut" | Add-Content $log
    & cmd.exe /d /c "`"$root\.venv\Scripts\python.exe`" -m studio.server >> `"$log`" 2>&1"     # cmd keeps the log in UTF-8
    "[$(Get-Date -Format s)] PeakCut stopped (exit $LASTEXITCODE); starting again in 10 s" | Add-Content $log
    if (Test-Path "$root\run\stopping") { break }
    Start-Sleep -Seconds 10
}
