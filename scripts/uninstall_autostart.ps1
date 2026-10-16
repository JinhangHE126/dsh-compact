# Remove login autostart (HKCU Run, Startup shortcut, optional schtasks).
$ErrorActionPreference = "Continue"
$TaskName = "dsh-compact-proxy"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $RunKey -Name $TaskName -ErrorAction SilentlyContinue
Write-Host "Removed HKCU Run value (if present): $TaskName"

$StartupCmd = Join-Path ([Environment]::GetFolderPath("Startup")) "dsh-compact-proxy.cmd"
if (Test-Path $StartupCmd) {
    Remove-Item $StartupCmd -Force
    Write-Host "Removed $StartupCmd"
}

cmd /c "schtasks /End /TN $TaskName >nul 2>&1"
cmd /c "schtasks /Delete /TN $TaskName /F >nul 2>&1"

$Launcher = Join-Path $env:USERPROFILE ".dsh-compact\run_proxy_task.cmd"
if (Test-Path $Launcher) {
    Remove-Item $Launcher -Force
    Write-Host "Removed $Launcher"
}

Write-Host "Autostart removed."
Write-Host "Stop a running proxy: netstat -ano | findstr :4001   then  taskkill /PID <pid> /F"
