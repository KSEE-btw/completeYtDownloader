# ytgrab — build a standalone host executable with PyInstaller
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_host.ps1
#
# Produces host\dist\ytgrab-host.exe and re-runs install.ps1 so Firefox
# launches the exe instead of the pythonw launcher.

[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$OneDir
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

& (Join-Path $PSScriptRoot "install.ps1") -Python $Python
