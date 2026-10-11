# ytgrab — install the native messaging host (Windows, no admin needed)
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# Registers the host under HKCU so Firefox can find it, and points the
# native-messaging manifest at either the built .exe (preferred) or a
# pythonw launcher.

[CmdletBinding()]
param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$root      = Split-Path -Parent $PSScriptRoot
$hostDir   = Join-Path $root "host"
$script    = Join-Path $hostDir "ytgrab_host.py"

if (-not (Test-Path $script)) {
  throw "could not find host\ytgrab_host.py under $root"
}

# --- locate python -----------------------------------------------------------
if (-not $Python) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python -or -not (Test-Path $Python)) {
  throw "python not found; pass -Python C:\path\to\python.exe"
}
$pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $Python }

# --- pick the executable Firefox will launch ---------------------------------
$exe      = Join-Path $hostDir "dist\ytgrab-host.exe"
$launcher = Join-Path $hostDir "ytgrab-launcher.bat"

if (Test-Path $exe) {
  $hostPath = $exe
  Write-Host "host executable : $exe"
} else {
  $bat = @"
@echo off
"$pythonw" "%~dp0ytgrab_host.py" %*
"@
  Set-Content -Path $launcher -Value $bat -Encoding ASCII
  $hostPath = $launcher
  Write-Host "host launcher   : $launcher  (pythonw)"
  Write-Host "  tip: run scripts\build_host.ps1 to produce a standalone .exe"
}

# --- write the native messaging manifest -------------------------------------
$manifestPath = Join-Path $hostDir "ytgrab.manifest.json"
$manifest = [ordered]@{
  name                = "ytgrab"
  description         = "ytgrab download + Premiere conversion host"
  path                = $hostPath
  type                = "stdio"
  allowed_extensions  = @("ytgrab@kunal.local")
}
$json = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($manifestPath,
  $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "manifest        : $manifestPath"

# --- register with Firefox ---------------------------------------------------
$key = "HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab"
New-Item -Path $key -Force | Out-Null
Set-Item -Path $key -Value $manifestPath
Write-Host "registry        : $key"

# --- sanity check ------------------------------------------------------------
Write-Host ""
Write-Host "checking the host..." -ForegroundColor Cyan
& $Python $script --ping | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "host responds OK" -ForegroundColor Green
} else {
  Write-Warning "host did not respond; run: $Python `"$script`" --ping"
}

Write-Host ""
Write-Host "Next steps" -ForegroundColor Cyan
Write-Host "  1. Firefox -> about:debugging#/runtime/this-firefox"
Write-Host "  2. 'Load Temporary Add-on...' -> pick extension\manifest.json"
Write-Host "  3. Open a YouTube watch page and click the blue arrow button."
Write-Host ""
Write-Host "Temporary add-ons vanish when Firefox restarts. For a permanent"
Write-Host "install, package with web-ext/Signing (see README.md)."
