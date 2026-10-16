# Install login autostart for dsh-compact proxy (no admin).
# Prefer HKCU Run (reliable without elevation). Also try schtasks ONLOGON.
$ErrorActionPreference = "Stop"

& "$PSScriptRoot\setup_home.ps1"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HomeDir = Join-Path $env:USERPROFILE ".dsh-compact"
$Pythonw = Join-Path $HomeDir "venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($cmd) { $Pythonw = $cmd.Source }
}
if (-not $Pythonw -or -not (Test-Path $Pythonw)) {
    throw "pythonw.exe not found. Run setup_home.ps1 or install Python."
}

$Proxy = Join-Path $Repo "proxy.py"
$TaskName = "dsh-compact-proxy"
$Launcher = Join-Path $HomeDir "run_proxy_task.cmd"

@"
@echo off
cd /d "$Repo"
"$Pythonw" "$Proxy"
"@ | Set-Content -Path $Launcher -Encoding ASCII

# --- Primary: HKCU Run (no admin) ---
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name $TaskName -Value "`"$Launcher`""
Write-Host "Registered HKCU Run: $TaskName -> $Launcher"

# --- Also drop a Startup folder shortcut for visibility ---
$Startup = [Environment]::GetFolderPath("Startup")
$StartupCmd = Join-Path $Startup "dsh-compact-proxy.cmd"
Copy-Item -Path $Launcher -Destination $StartupCmd -Force
Write-Host "Copied launcher to Startup: $StartupCmd"

# --- Optional: scheduled task (may fail without rights; non-fatal) ---
cmd /c "schtasks /Delete /TN $TaskName /F >nul 2>&1"
cmd /c "schtasks /Create /TN $TaskName /SC ONLOGON /RL LIMITED /F /TR `"$Launcher`" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Also created scheduled task: $TaskName"
} else {
    Write-Host "Scheduled task skipped (Access denied is OK); HKCU Run + Startup are enough."
}

Write-Host ""
Write-Host "Start now:"
Write-Host "  Start-Process -FilePath `"$Launcher`""
Write-Host "  curl.exe http://127.0.0.1:4001/health"
Write-Host ""
Write-Host "Logs: $HomeDir\logs\proxy.log"
Write-Host "DSH Base URL -> http://127.0.0.1:4001/v1 then restart DSH."
Write-Host ""
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("COMPANY_LLM_KEY", "User"))) {
    Write-Host "WARNING: Set User COMPANY_LLM_KEY before relying on autostart:"
    Write-Host '  [Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY", "sk-...", "User")'
}
