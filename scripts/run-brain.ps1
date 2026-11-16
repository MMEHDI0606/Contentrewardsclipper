# The brain: an interactive Claude Code session that answers PeakCut's AI requests, started again if it exits.
# Runs in its own window (opened by start.ps1). Close this window, or run stop.cmd, to stop it.
param([ValidateSet("loop", "channel")] [string]$Mode = "loop", [string]$Model = "")
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$brain = Join-Path $root "brain"
New-Item -ItemType Directory -Force -Path "$root\run" | Out-Null
$PID | Set-Content "$root\run\brain-runner.pid"
$Host.UI.RawUI.WindowTitle = "PeakCut brain"

# The model is the one chosen in PeakCut (Settings, AI); sonnet unless something else was picked there.
if (-not $Model) {
    $Model = & "$root\.venv\Scripts\python.exe" -c "from studio import ai; m = ai.models(); print(m[0] if ai.provider() == 'claude_session' and m else 'sonnet')" 2>$null
    if (-not $Model) { $Model = "sonnet" }
}
$dataDir = & "$root\.venv\Scripts\python.exe" -c "from studio import config; print(config.DATA_DIR)" 2>$null

# Only this session sees these. They keep it small and closed: no CLAUDE.md files (its role is passed below),
# no claude.ai connectors, no prompt suggestions or recaps, no auto memory.
$env:PEAKCUT_URL = "http://127.0.0.1:8765"
if ($dataDir) { $env:PEAKCUT_BRAIN_TOKEN_FILE = Join-Path $dataDir "brain.token" }
$env:CLAUDE_CODE_DISABLE_CLAUDE_MDS = "1"
$env:ENABLE_CLAUDEAI_MCP_SERVERS = "false"
$env:CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION = "false"
$env:CLAUDE_CODE_ENABLE_AWAY_SUMMARY = "0"
$env:CLAUDE_CODE_DISABLE_AUTO_MEMORY = "1"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue      # never bill an API key by accident

$role = Get-Content -Raw (Join-Path $brain "CLAUDE.md")
# The npm "claude.cmd" shim goes through cmd.exe, which cuts a many-line argument short: call claude.exe itself.
$claude = "claude"
$shim = Get-Command claude.cmd -ErrorAction SilentlyContinue
if ($shim) {
    $exe = Join-Path (Split-Path $shim.Source) "node_modules\@anthropic-ai\claude-code\bin\claude.exe"
    if (Test-Path $exe) { $claude = $exe }
}
$args_ = @("--model", $Model, "--effort", "low", "--name", "peakcut-brain",
           "--setting-sources", "project", "--permission-mode", "dontAsk",
           "--strict-mcp-config", "--mcp-config", "mcp.json",
           "--tools", "CronCreate,CronList,CronDelete,ScheduleWakeup",
           "--append-system-prompt", $role)
if ($Mode -eq "channel") {
    $env:PEAKCUT_BRAIN_CHANNEL = "1"
    $args_ += @("--dangerously-load-development-channels", "server:peakcut")
    $first = "/loop 10m"            # a slow safety net: channel events can be dropped silently
} else {
    $first = "/loop 5m"             # each turn waits up to 4 minutes inside next_request, so work is picked up at once
}

Set-Location $brain
while ($true) {
    Write-Host "Starting the PeakCut brain ($Model, $Mode mode). Close this window to stop it." -ForegroundColor Cyan
    Write-Host "First start only: Claude asks whether to trust this folder. Choose Yes; its tools do not load until you do." -ForegroundColor Cyan
    $t0 = Get-Date
    & $claude @args_ $first
    if (Test-Path "$root\run\stopping") { break }
    $ran = ((Get-Date) - $t0).TotalSeconds
    $wait = if ($ran -lt 60) { 120 } else { 15 }      # exiting at once (a login problem?) is not retried in a tight loop
    Write-Host "The brain stopped after $([int]$ran) s. Starting it again in $wait s (close this window to stop)." -ForegroundColor Yellow
    Start-Sleep -Seconds $wait
}
