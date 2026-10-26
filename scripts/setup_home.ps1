# Create ~/.dsh-compact layout (venv optional; logs/samples/notes).
$ErrorActionPreference = "Stop"
$HomeDir = Join-Path $env:USERPROFILE ".dsh-compact"
@(
    $HomeDir,
    (Join-Path $HomeDir "logs"),
    (Join-Path $HomeDir "samples"),
    (Join-Path $HomeDir "notes")
) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

# Production defaults (User scope). Does NOT set COMPANY_LLM_KEY.
function Set-UserEnvIfMissing([string]$Name, [string]$Value) {
    $cur = [Environment]::GetEnvironmentVariable($Name, "User")
    if ([string]::IsNullOrWhiteSpace($cur)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "User")
        Write-Host "Set User $Name=$Value"
    } else {
        Write-Host "Keep User $Name=$cur"
    }
}

Set-UserEnvIfMissing "COMPANY_LLM_BASE"  "http://llm-gateway.example:4000/v1"
Set-UserEnvIfMissing "COMPANY_LLM_MODEL" "company-code"
Set-UserEnvIfMissing "DASH_COMPACT_PORT" "4001"
Set-UserEnvIfMissing "DASH_COMPACT_TRIGGER" "180000"
Set-UserEnvIfMissing "DASH_COMPACT_TARGET" "80000"
Set-UserEnvIfMissing "DASH_COMPACT_MICRO_KEEP" "5"
Set-UserEnvIfMissing "DASH_COMPACT_MAX_CONCURRENT" "1"
Set-UserEnvIfMissing "DASH_COMPACT_FAILURE_COOLDOWN" "300"
Set-UserEnvIfMissing "DASH_COMPACT_DISABLE" "0"
Set-UserEnvIfMissing "DASH_COMPACT_LOG_LEVEL" "INFO"
Set-UserEnvIfMissing "DASH_COMPACT_IDLE_WAIT_S" "180"
Set-UserEnvIfMissing "DASH_COMPACT_SUMMARIZE_RETRIES" "4"

# Force production thresholds (hand-test may have left process env at 2000)
[Environment]::SetEnvironmentVariable("DASH_COMPACT_TRIGGER", "180000", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_TARGET", "80000", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "0", "User")
Write-Host "Forced User DASH_COMPACT_TRIGGER=180000 TARGET=80000 DISABLE=0"

$VenvPy = Join-Path $HomeDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "No venv at $VenvPy — creating with py -3 ..."
    py -3 -m venv (Join-Path $HomeDir "venv")
    & (Join-Path $HomeDir "venv\Scripts\python.exe") -m pip install -r (Join-Path $PSScriptRoot "..\requirements.txt")
}

Write-Host "Home ready: $HomeDir"
Write-Host "Ensure COMPANY_LLM_KEY is set (User env) before starting the proxy."
Write-Host "Recommended: also set DASH_COMPACT_SUMMARIZE_KEY (separate key) so summarize does not steal chat parallel quota."
Write-Host "Full guide: README.md (Quick Start)"
