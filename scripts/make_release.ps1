# ytgrab -- assemble a self-contained Windows release bundle
#
#   powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Sign
#
# Produces:
#   release\ytgrab-<version>\               (the files, ready to inspect)
#   release\ytgrab-<version>-windows.zip    (hand this to anyone)
#
# The bundle contains everything a new user needs: the standalone host exe, an
# installer that needs no admin rights, and (with -Sign) a signed .xpi that
# installs permanently.

[CmdletBinding()]
param(
  [switch]$Sign,
  [switch]$BumpPatch,
  [switch]$SkipHostBuild,
  [string]$ApiKey    = $env:AMO_JWT_ISSUER,
  [string]$ApiSecret = $env:AMO_JWT_SECRET,
  [string]$Python    = "",
  [string]$WebExt    = "8"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=== ytgrab release build ===" -ForegroundColor Cyan

# --- 1. standalone host exe --------------------------------------------------
if ($SkipHostBuild) {
  Write-Host "skipping host build (-SkipHostBuild)"
} else {
  Write-Host ""
  Write-Host "--- building host ---" -ForegroundColor Cyan
  & (Join-Path $PSScriptRoot "build_host.ps1") -Python $Python -NoInstall
}

# --- 2. package (and optionally sign) the extension --------------------------
Write-Host ""
Write-Host "--- packaging extension ---" -ForegroundColor Cyan
$pkgArgs = @{ WebExt = $WebExt }
if ($Sign)      { $pkgArgs.Sign      = $true }
if ($BumpPatch) { $pkgArgs.BumpPatch = $true }
if ($ApiKey)    { $pkgArgs.ApiKey    = $ApiKey }
if ($ApiSecret) { $pkgArgs.ApiSecret = $ApiSecret }
& (Join-Path $PSScriptRoot "package.ps1") @pkgArgs

$version = (Get-Content (Join-Path $root "extension\manifest.json") -Raw |
              ConvertFrom-Json).version

# --- 3. stage the bundle -----------------------------------------------------
$stage = Join-Path $root "release\ytgrab-$version"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$exe = Join-Path $root "host\dist\ytgrab-host.exe"
if (-not (Test-Path $exe)) { throw "host exe not found at $exe (build it first)" }

Copy-Item $exe                                    (Join-Path $stage "ytgrab-host.exe")  -Force
Copy-Item (Join-Path $root "host\ytgrab_host.py") (Join-Path $stage "ytgrab_host.py")   -Force
Copy-Item (Join-Path $PSScriptRoot "setup.ps1")     (Join-Path $stage "setup.ps1")     -Force
Copy-Item (Join-Path $PSScriptRoot "install.ps1") (Join-Path $stage "install.ps1")     -Force
Copy-Item (Join-Path $PSScriptRoot "uninstall.ps1") (Join-Path $stage "uninstall.ps1") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $stage "lib") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "lib\firefox.ps1") (Join-Path $stage "lib\firefox.ps1") -Force

$xpiName = $null
$signed = Get-ChildItem (Join-Path $root "web-ext-artifacts\*.xpi") -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "*-unsigned.xpi" } |
            Sort-Object LastWriteTime | Select-Object -Last 1
if ($signed) {
  $xpiName = "ytgrab-$version.xpi"
  Copy-Item $signed.FullName (Join-Path $stage $xpiName) -Force
} else {
  $unsigned = Get-ChildItem (Join-Path $root "web-ext-artifacts\*-unsigned.xpi") -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime | Select-Object -Last 1
  if ($unsigned) {
    $xpiName = "ytgrab-$version-UNSIGNED.xpi"
    Copy-Item $unsigned.FullName (Join-Path $stage $xpiName) -Force
  }
}

# --- 4. quick-start readme ----------------------------------------------------
$installLine = if ($xpiName -and $xpiName -notlike "*UNSIGNED*") {
  @"
Install the extension:
     - Open Firefox and drag $xpiName into the window, or
     - about:addons -> gear icon -> 'Install Add-on From File...' -> pick $xpiName
   It is signed by Mozilla, so it stays installed across restarts.
"@
} else {
  @"
Install the extension (this build is UNSIGNED, so it is temporary):
     - about:debugging#/runtime/this-firefox -> 'Load Temporary Add-on...'
       -> pick $xpiName in this folder
   Unsigned builds only last until Firefox quits. A permanent install needs a
   Mozilla-signed .xpi, which only whoever built this bundle can produce
   (scripts\package.ps1 -Sign) -- ask them for the signed one.
"@
}

$readme = @"
ytgrab -- quick start (Windows)
===============================

1. Run the interactive installer:

     powershell -ExecutionPolicy Bypass -File setup.ps1

   It installs yt-dlp, then ffmpeg, then the local helper, and finally asks
   which browser to add ytgrab to. Nothing needs administrator rights, every
   step is a prompt you can decline, and it is safe to run again.

2. $installLine

3. Open any YouTube video and click the blue arrow button at the bottom right.
   The toolbar icon works on any site yt-dlp understands.

By hand instead?  powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDeps

Configuration and full docs: see README.md in the project repository.
To remove everything:  powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge
   (add -Deep with Firefox closed to clear the last profile bookkeeping)
"@
[System.IO.File]::WriteAllText((Join-Path $stage "READ-ME-FIRST.txt"),
  $readme, (New-Object System.Text.UTF8Encoding($false)))

# --- 5. zip -------------------------------------------------------------------
$zip = Join-Path $root "release\ytgrab-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -Force

Write-Host ""
Write-Host "=== done ===" -ForegroundColor Green
Write-Host "folder : $stage"
Write-Host "zip    : $zip"
Write-Host ""
if ($signed) {
  Write-Host "The .xpi inside is Mozilla-signed and installs permanently." -ForegroundColor Green
} else {
  Write-Host "No signed .xpi in this bundle -- rebuild with -Sign for a permanent install." -ForegroundColor Yellow
}
