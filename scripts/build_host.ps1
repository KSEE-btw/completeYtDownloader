# ytgrab -- build a standalone executable with PyInstaller
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_host.ps1
#
# Produces host\dist\ytgrab-host.exe.  One binary serves both roles: run with
# no arguments it is the native-messaging host Firefox talks to; run with --ui
# it is the download queue window.  The script re-runs install.ps1 so Firefox
# launches the exe instead of the pythonw launcher.

[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$OneDir,
  [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$hostDir = Join-Path $root "host"

if (-not $Python) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python) { throw "python not found; pass -Python C:\path\to\python.exe" }

Write-Host "ensuring PyInstaller is available..." -ForegroundColor Cyan
& $Python -m pip install --upgrade --quiet pyinstaller

$mode = if ($OneDir) { "--onedir" } else { "--onefile" }
$spec = Join-Path $hostDir "build"
New-Item -ItemType Directory -Force -Path $spec | Out-Null

$args = @(
  "-m", "PyInstaller",
  "--noconfirm", "--clean",
  $mode,
  "--noconsole",
  "--name", "ytgrab-host",
  "--hidden-import", "tkinter",
  "--hidden-import", "tkinter.ttk",
  "--hidden-import", "tkinter.messagebox",
  "--distpath", (Join-Path $hostDir "dist"),
  "--workpath", (Join-Path $hostDir "build"),
  "--specpath", $spec,
  (Join-Path $hostDir "ytgrab_host.py")
)

Write-Host "building: $Python $($args -join ' ')" -ForegroundColor Cyan
& $Python @args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $hostDir "dist\ytgrab-host.exe"
Write-Host "built $exe" -ForegroundColor Green

if ($NoInstall) {
  Write-Host "(skipping install.ps1 because -NoInstall was given)"
} else {
  & (Join-Path $PSScriptRoot "install.ps1") -Python $Python
}
