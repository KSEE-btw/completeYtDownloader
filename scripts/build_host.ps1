# ytgrab -- build the standalone Windows executables with PyInstaller
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_host.ps1
#
# Produces two binaries in host\dist:
#   ytgrab-host.exe  windowless: the native-messaging host Firefox talks to
#                    (no arguments) and the download queue window (--ui)
#   ytgrab.exe       console CLI: update / notes / version / ui
#
# The script re-runs install.ps1 so Firefox launches the exe instead of the
# pythonw launcher, and so the CLI lands on your PATH.

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

$mode     = if ($OneDir) { "--onedir" } else { "--onefile" }
$distPath = Join-Path $hostDir "dist"
$workPath = Join-Path $hostDir "build"
$specPath = Join-Path $hostDir "build"
New-Item -ItemType Directory -Force -Path $specPath | Out-Null

function Build-YtgrabExe {
  param([string]$Name, [string]$Entry, [string[]]$Extra)
  $pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean",
    $mode,
    "--name", $Name,
    "--distpath", $distPath,
    "--workpath", $workPath,
    "--specpath", $specPath
  ) + $Extra + @((Join-Path $hostDir $Entry))
  Write-Host "building: $Python $($pyArgs -join ' ')" -ForegroundColor Cyan
  & $Python @pyArgs
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Name" }
}

# The host is a GUI app: no console flashes when Firefox launches it, and the
# queue window does not drag a console along.
Build-YtgrabExe -Name "ytgrab-host" -Entry "ytgrab_host.py" -Extra @(
  "--noconsole",
  "--hidden-import", "tkinter",
  "--hidden-import", "tkinter.ttk",
  "--hidden-import", "tkinter.messagebox"
)

# The CLI is a console app: it prints progress and release notes.
Build-YtgrabExe -Name "ytgrab" -Entry "ytgrab_cli.py" -Extra @()

foreach ($name in @("ytgrab-host.exe", "ytgrab.exe")) {
  $exe = Join-Path $distPath $name
  if (-not (Test-Path $exe)) { throw "expected $exe was not produced" }
  Write-Host "built $exe" -ForegroundColor Green
}

if ($NoInstall) {
  Write-Host "(skipping install.ps1 because -NoInstall was given)"
} else {
  & (Join-Path $PSScriptRoot "install.ps1") -Python $Python
}
