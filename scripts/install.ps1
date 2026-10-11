# ytgrab -- install the native messaging host (Windows, no admin needed)
#
#   from a checkout:        powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   from a release bundle:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Copies the host into %LOCALAPPDATA%\ytgrab (so it keeps working even if you
# later delete the folder you downloaded), writes the native-messaging manifest,
# and registers it under HKCU so Firefox can launch it. No admin rights needed.
#
# Options:
#   -InstallDeps        run "winget install" for any missing yt-dlp / ffmpeg
#   -HostExe <path>     use a specific ytgrab-host.exe
#   -Python  <path>     python to use for the pythonw fallback (no exe present)

[CmdletBinding()]
param(
  [string]$HostExe = "",
  [string]$Python = "",
  [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

$here       = $PSScriptRoot
$installDir = Join-Path $env:LOCALAPPDATA "ytgrab"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

Write-Host ""
Write-Host "ytgrab host installer" -ForegroundColor Cyan
Write-Host "  installing to $installDir"
Write-Host ""

# --- 1. locate the host executable ------------------------------------------
if (-not $HostExe) {
  foreach ($c in @(
      (Join-Path $here "ytgrab-host.exe"),
      (Join-Path $here "host\dist\ytgrab-host.exe"),
      (Join-Path $here "dist\ytgrab-host.exe"),
      (Join-Path (Split-Path -Parent $here) "host\dist\ytgrab-host.exe"))) {
    if (Test-Path $c) { $HostExe = $c; break }
  }
}

$targetExe = Join-Path $installDir "ytgrab-host.exe"

if ($HostExe -and (Test-Path $HostExe)) {
  Copy-Item -Path $HostExe -Destination $targetExe -Force
  Write-Host "host executable : $targetExe" -ForegroundColor Green
} else {
  # No exe: fall back to running the Python source through pythonw.
  if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source }
  }
  $pySource = $null
  foreach ($c in @((Join-Path $here "ytgrab_host.py"),
                   (Join-Path $here "host\ytgrab_host.py"))) {
    if (Test-Path $c) { $pySource = $c; break }
  }
  if (-not $pySource) {
    throw "found neither ytgrab-host.exe nor ytgrab_host.py; pass -HostExe <path>"
  }
  if (-not $Python -or -not (Test-Path $Python)) {
    throw "no host exe and python was not found; pass -Python C:\path\to\python.exe"
  }
  $pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
  if (-not (Test-Path $pythonw)) { $pythonw = $Python }

  Copy-Item -Path $pySource -Destination (Join-Path $installDir "ytgrab_host.py") -Force
  $bat = "@echo off`r`n`"$pythonw`" `"%~dp0ytgrab_host.py`" %*"
  Set-Content -Path (Join-Path $installDir "ytgrab-launcher.bat") -Value $bat -Encoding ASCII
  $targetExe = Join-Path $installDir "ytgrab-launcher.bat"
  Write-Host "host launcher   : $targetExe  (pythonw)" -ForegroundColor Yellow
  Write-Host "  tip: build ytgrab-host.exe to avoid needing Python" -ForegroundColor DarkGray
}

# --- 2. write the native messaging manifest ---------------------------------
# Firefox wants a BOM-less UTF-8 JSON file at the path stored in the registry.
$manifestPath = Join-Path $installDir "ytgrab.manifest.json"
$manifest = [ordered]@{
  name               = "ytgrab"
  description        = "ytgrab download + Premiere conversion host"
  path               = $targetExe
  type               = "stdio"
  allowed_extensions = @("ytgrab@kunal.local")
}
$json = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText(
  $manifestPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "manifest        : $manifestPath"

# --- 3. register with Firefox (per-user, no admin) --------------------------
$key = "HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab"
New-Item -Path $key -Force | Out-Null
Set-Item -Path $key -Value $manifestPath
Write-Host "registry        : $key"

# --- 4. external tools the host shells out to -------------------------------
Write-Host ""
$missing = @()
if (-not (Get-Command yt-dlp -ErrorAction SilentlyContinue)) { $missing += "yt-dlp" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $missing += "ffmpeg" }
if ($missing.Count) {
  Write-Host "missing tools   : $($missing -join ', ')" -ForegroundColor Yellow
  Write-Host "  install with  : winget install yt-dlp.yt-dlp Gyan.FFmpeg"
  if ($InstallDeps) {
    Write-Host "  installing via winget..." -ForegroundColor Cyan
    winget install --id yt-dlp.yt-dlp --accept-source-agreements --accept-package-agreements
    winget install --id Gyan.FFmpeg   --accept-source-agreements --accept-package-agreements
  } else {
    Write-Host "  (re-run with -InstallDeps to install them automatically)"
  }
} else {
  Write-Host "external tools  : yt-dlp + ffmpeg found on PATH" -ForegroundColor Green
}

# --- 5. sanity check: launch the host exactly like Firefox would -------------
Write-Host ""
Write-Host "checking the host..." -ForegroundColor Cyan
$ok = $false
try {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  if ($targetExe -like "*.bat") {
    $psi.FileName  = "cmd.exe"
    $psi.Arguments = "/c `"$targetExe`" --ping"
  } else {
    $psi.FileName  = $targetExe
    $psi.Arguments = "--ping"
  }
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError  = $true
  $psi.UseShellExecute        = $false
  $psi.CreateNoWindow         = $true
  $p = [System.Diagnostics.Process]::Start($psi)
  $null = $p.StandardOutput.ReadToEnd()
  $null = $p.StandardError.ReadToEnd()
  $p.WaitForExit(20000) | Out-Null
  $ok = ($p.HasExited -and $p.ExitCode -eq 0)
} catch {
  Write-Warning $_
}
if ($ok) {
  Write-Host "host responds OK" -ForegroundColor Green
} else {
  Write-Warning "host did not respond; see the Troubleshooting section in README.md"
}

# --- 6. next steps -----------------------------------------------------------
Write-Host ""
Write-Host "Next steps" -ForegroundColor Cyan
Write-Host "  1. Install the extension:"
Write-Host "       about:addons  -> gear icon -> 'Install Add-on From File...'"
Write-Host "       (or drag a signed ytgrab .xpi into a Firefox window)"
Write-Host "     No signed .xpi? Load one temporarily instead:"
Write-Host "       about:debugging#/runtime/this-firefox -> 'Load Temporary Add-on...'"
Write-Host "       -> pick ytgrab-<version>.xpi (or extension\manifest.json in a checkout);"
Write-Host "          temporary add-ons only survive until Firefox quits"
Write-Host "  2. Open a YouTube video and click the blue arrow button at the bottom right."
Write-Host ""
