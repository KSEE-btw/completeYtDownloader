# ytgrab -- install the native messaging host (Windows, no admin needed)
#
#   from a checkout:        powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   from a release bundle:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Copies the host into %LOCALAPPDATA%\ytgrab (so it keeps working even if you
# later delete the folder you downloaded), writes the native-messaging manifest,
# and registers it under HKCU so Firefox can launch it. No admin rights needed.
#
# Usually you do not run this by hand: scripts\setup.ps1 is the friendly
# interactive installer and calls this file. Use this one for scripting/CI.
#
# Options:
#   -InstallDeps    run "winget install" for any missing yt-dlp / ffmpeg
#   -SkipDeps       do not look for yt-dlp / ffmpeg at all
#   -Quiet          minimal output (used by setup.ps1)
#   -HostExe <path> use a specific ytgrab-host.exe
#   -Python  <path> python to use for the pythonw fallback (no exe present)

[CmdletBinding()]
param(
  [string]$HostExe = "",
  [string]$Python = "",
  [switch]$InstallDeps,
  [switch]$SkipDeps,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$here       = $PSScriptRoot
$installDir = Join-Path $env:LOCALAPPDATA "ytgrab"

# shared Firefox helpers (the signed-.xpi finder for the closing message)
$lib = Join-Path $here "lib\firefox.ps1"
if (Test-Path $lib) { . $lib }

# The version of the build being installed: a release bundle carries version.txt,
# a checkout has it in the extension manifest.
function Get-YtgrabVersion {
  foreach ($c in @((Join-Path $here "version.txt"),
                   (Join-Path $here "extension\manifest.json"),
                   (Join-Path $here "..\extension\manifest.json"))) {
    if (Test-Path $c) {
      if ($c -like "*.txt") {
        $v = (Get-Content $c -Raw).Trim()
        if ($v) { return $v }
      } else {
        $v = (Get-Content $c -Raw | ConvertFrom-Json).version
        if ($v) { return $v }
      }
    }
  }
  return ""
}

# Put the install folder on the *user* PATH so "ytgrab update" works anywhere.
# Reports $true only when it actually changed something.
function Add-YtgrabToUserPath {
  param([string]$Dir)
  try {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $current) { $current = "" }
    $parts = @($current.Split(";") | Where-Object { $_ -ne "" })
    if ($parts -contains $Dir) { return $false }
    [Environment]::SetEnvironmentVariable("Path", (@($parts) + $Dir -join ";"), "User")
    return $true
  } catch {
    Write-Warning "could not update your user PATH: $_"
    return $false
  }
}

# How to describe getting a permanently-installable build: package.ps1 exists in
# a checkout but not in a release bundle, which only carries the signed .xpi.
$signHint = if (Test-Path (Join-Path $here "package.ps1")) {
  "scripts\package.ps1 -Sign  (free AMO keys; see README.md)"
} else {
  "ask whoever sent you this bundle for the signed .xpi"
}

# What about:debugging should be pointed at: the source manifest in a checkout,
# or the packaged unsigned .xpi a release bundle actually carries.
$tempHint = if ((Test-Path (Join-Path $here "extension\manifest.json")) -or
                (Test-Path (Join-Path $here "..\extension\manifest.json"))) {
  "extension\manifest.json"
} else {
  $u = Get-ChildItem (Join-Path $here "*unsigned*.xpi") -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime | Select-Object -Last 1
  if ($u) { $u.Name } else { "the .xpi in this folder" }
}
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

if (-not $Quiet) {
  Write-Host ""
  Write-Host "completeYtDownloader host installer" -ForegroundColor Cyan
  Write-Host "  installing to $installDir"
  Write-Host ""
}

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
  foreach ($c in @((Join-Path $here "src\ytgrab\entrypoints\host.py"),
                   (Join-Path (Split-Path -Parent $here) "src\ytgrab\entrypoints\host.py"))) {
    if (Test-Path $c) { $pySource = $c; break }
  }
  if (-not $pySource) {
    throw "found neither ytgrab-host.exe nor src\ytgrab\entrypoints\host.py; pass -HostExe <path>"
  }
  if (-not $Python -or -not (Test-Path $Python)) {
    throw "no host exe and python was not found; pass -Python C:\path\to\python.exe"
  }
  $pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
  if (-not (Test-Path $pythonw)) { $pythonw = $Python }

  # run the package straight from the checkout: PYTHONPATH points at src\
  $srcDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $pySource))
  $bat = "@echo off`r`nset `"PYTHONPATH=$srcDir`"`r`n`"$pythonw`" -m ytgrab.entrypoints.host %*"
  Set-Content -Path (Join-Path $installDir "ytgrab-launcher.bat") -Value $bat -Encoding ASCII
  $targetExe = Join-Path $installDir "ytgrab-launcher.bat"
  Write-Host "host launcher   : $targetExe  (pythonw -m ytgrab)" -ForegroundColor Yellow
  Write-Host "  tip: build ytgrab-host.exe to avoid needing Python" -ForegroundColor DarkGray
}

# --- 1b. the console CLI (ytgrab update / notes) ----------------------------
# Optional: a checkout without a built exe simply has no CLI to install.
$cliExe = $null
foreach ($c in @((Join-Path $here "ytgrab.exe"),
                 (Join-Path $here "host\dist\ytgrab.exe"),
                 (Join-Path $here "dist\ytgrab.exe"),
                 (Join-Path (Split-Path -Parent $here) "host\dist\ytgrab.exe"))) {
  if (Test-Path $c) { $cliExe = $c; break }
}
if ($cliExe) {
  $targetCli = Join-Path $installDir "ytgrab.exe"
  Copy-Item -Path $cliExe -Destination $targetCli -Force
  Write-Host "command line    : $targetCli" -ForegroundColor Green
}

$version = Get-YtgrabVersion
if ($version) {
  [System.IO.File]::WriteAllText((Join-Path $installDir "version.txt"),
    "$version`n", (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "version         : $version"
}

if ($cliExe) {
  if (Add-YtgrabToUserPath $installDir) {
    Write-Host "PATH            : added $installDir to your user PATH" -ForegroundColor Green
    Write-Host "                  open a new terminal, then run:  ytgrab update"
  } else {
    Write-Host "PATH            : $installDir is already on your user PATH"
  }
  $env:Path = "$env:Path;$installDir"   # so this session sees it too
}

# --- 2. write the native messaging manifest ---------------------------------
# Firefox wants a BOM-less UTF-8 JSON file at the path stored in the registry.
$manifestPath = Join-Path $installDir "ytgrab.manifest.json"
$manifest = [ordered]@{
  name               = "ytgrab"
  description        = "completeYtDownloader download + Premiere conversion host"
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
if (-not $SkipDeps) {
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
  Write-Warning "host did not respond; see the troubleshooting notes in the project documentation"
}

# --- 6. what is left ---------------------------------------------------------
# Deliberately honest about the one thing this script cannot do: release Firefox
# only keeps an add-on that Mozilla has signed, and signing needs AMO keys.
if (-not $Quiet) {
  $signed = $null
  if (Get-Command Find-YtgrabSignedXpi -ErrorAction SilentlyContinue) {
    $signed = Find-YtgrabSignedXpi -StartDir $here
  }

  Write-Host ""
  Write-Host "Helper done -- the extension is the last piece." -ForegroundColor Cyan
  Write-Host ""
  if ($signed) {
    Write-Host "  Signed package found: $(Split-Path -Leaf $signed)"
    Write-Host "  Drag it into a Firefox window, or use"
    Write-Host "  about:addons -> gear -> 'Install Add-on From File...'"
    Write-Host "  Firefox will keep it across restarts."
  } else {
    Write-Host "  No Mozilla-signed .xpi is next to this script, and release Firefox"
    Write-Host "  will not keep an add-on it cannot verify. Two options:"
    Write-Host ""
    Write-Host "    permanent : $signHint"
    Write-Host "    right now : about:debugging#/runtime/this-firefox"
    Write-Host "                  -> 'Load Temporary Add-on...' -> $tempHint"
    Write-Host "                  (works instantly; goes away when Firefox quits)"
  }
  Write-Host ""
  if ($cliExe) {
    Write-Host "  From any new terminal:  ytgrab update   (check for updates)"
    Write-Host "                          ytgrab ui       (open the download queue)"
    Write-Host ""
  }
  Write-Host "Then open any YouTube video and click the blue arrow button."
  Write-Host ""
}
