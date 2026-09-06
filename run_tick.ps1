$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$logFile = Join-Path $PSScriptRoot "tick.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "`n--- $timestamp ---" -Encoding UTF8
& python -m funnel_doctor.seed tick 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding UTF8
