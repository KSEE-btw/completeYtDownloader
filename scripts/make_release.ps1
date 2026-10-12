# ytgrab -- assemble a self-contained Windows release bundle
#
#   powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Sign
#
# Produces:
#   release\ytgrab-<version>\               (the files, ready to inspect)
#   release\ytgrab-<version>-windows.zip    (hand this to anyone)
#   release\ytgrab-<version>.xpi            (the signed add-on, if -Sign worked)
#   release\SHA256SUMS.txt                  (checksum for "ytgrab update")
#   release\updates.json                    (Firefox auto-update manifest)
#
# The bundle contains everything a new user needs: the windowless host, the
# console CLI, an installer that needs no admin rights, the release notes, and
# (with -Sign) a signed .xpi that installs permanently.

[CmdletBinding()]
param(
  [switch]$Sign,
  [switch]$BumpPatch,
  [switch]$SkipHostBuild,
  [string]$ApiKey    = $env:AMO_JWT_ISSUER,
  [string]$ApiSecret = $env:AMO_JWT_SECRET,
  [string]$Python    = "",
  [string]$WebExt    = "8",
  [string]$Repo      = "KSEE-btw/completeYtDownloader"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=== completeYtDownloader release build ===" -ForegroundColor Cyan

# --- 1. standalone executables -----------------------------------------------
if ($SkipHostBuild) {
  Write-Host "skipping host build (-SkipHostBuild)"
} else {
  Write-Host ""
  Write-Host "--- building host + cli ---" -ForegroundColor Cyan
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

foreach ($pair in @(
    @("host\dist\ytgrab-host.exe", "ytgrab-host.exe"),
    @("host\dist\ytgrab.exe",      "ytgrab.exe"),
    @("scripts\setup.ps1",         "setup.ps1"),
    @("scripts\install.ps1",       "install.ps1"),
    @("scripts\uninstall.ps1",     "uninstall.ps1"))) {
  $from = Join-Path $root $pair[0]
  if (-not (Test-Path $from)) { throw "missing $from (build it first)" }
  Copy-Item $from (Join-Path $stage $pair[1]) -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage "lib") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "lib\firefox.ps1") (Join-Path $stage "lib\firefox.ps1") -Force

# the single source of truth for "what is installed"
[System.IO.File]::WriteAllText((Join-Path $stage "version.txt"),
  "$version`n", (New-Object System.Text.UTF8Encoding($false)))

# release notes: docs\releases\v<version>.md, shipped and used as the release body
$notesFrom = Join-Path $root "docs\releases\v$version.md"
if (Test-Path $notesFrom) {
  Copy-Item $notesFrom (Join-Path $stage "RELEASE-NOTES.md") -Force
} else {
  Write-Host "warning: no docs\releases\v$version.md; the bundle has no notes" -ForegroundColor Yellow
}

# --- 4. the signed (or unsigned) extension -----------------------------------
$xpiName = $null
$signed = Get-ChildItem (Join-Path $root "web-ext-artifacts\*.xpi") -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "*-unsigned.xpi" } |
            Sort-Object LastWriteTime | Select-Object -Last 1
if ($signed) {
  $xpiName = "ytgrab-$version.xpi"
  Copy-Item $signed.FullName (Join-Path $stage $xpiName) -Force
  Copy-Item $signed.FullName (Join-Path $root "release\$xpiName") -Force
} else {
  $unsigned = Get-ChildItem (Join-Path $root "web-ext-artifacts\*-unsigned.xpi") -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime | Select-Object -Last 1
  if ($unsigned) {
    $xpiName = "ytgrab-$version-UNSIGNED.xpi"
    Copy-Item $unsigned.FullName (Join-Path $stage $xpiName) -Force
  }
}

# --- 5. quick-start readme ----------------------------------------------------
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
completeYtDownloader -- quick start (Windows)
=============================================

1. Run the interactive installer:

     powershell -ExecutionPolicy Bypass -File setup.ps1

   It installs yt-dlp, then ffmpeg, then the local helper, and finally asks
   which browser to add completeYtDownloader to. Nothing needs administrator rights, every
   step is a prompt you can decline, and it is safe to run again.

2. $installLine

3. Open any YouTube video and click the blue arrow button at the bottom right.
   The toolbar icon works on any site yt-dlp understands. Downloads run in a
   small queue window, so closing the browser popup does not stop them.

Keeping up to date -- from any new terminal:

     ytgrab update     download and install the newest version, then show
                       what changed
     ytgrab notes      show the notes for the installed version
     ytgrab ui         open (or focus) the download queue window

By hand instead?  powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDeps

Configuration and full docs: see README.md in the project repository.
To remove everything:  powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge
   (add -Deep with Firefox closed to clear the last profile bookkeeping)
"@
[System.IO.File]::WriteAllText((Join-Path $stage "READ-ME-FIRST.txt"),
  $readme, (New-Object System.Text.UTF8Encoding($false)))

# --- 6. zip + checksum -------------------------------------------------------
$zip = Join-Path $root "release\ytgrab-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -Force

$hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
[System.IO.File]::WriteAllText((Join-Path $root "release\SHA256SUMS.txt"),
  "$hash  $(Split-Path -Leaf $zip)`n", (New-Object System.Text.UTF8Encoding($false)))

# --- 7. Firefox auto-update manifest -----------------------------------------
# Only a signed build can be auto-updated. update_url in the extension manifest
# points at this file on main, so "ytgrab update" is not the only path: Firefox
# also picks up new versions on its own.
if ($signed -and $xpiName -and $xpiName -notlike "*UNSIGNED*") {
  $link = "https://github.com/$Repo/releases/download/v$version/$xpiName"
  $updatesJson = @"
{
  "addons": {
    "ytgrab@kunal.local": {
      "updates": [
        {
          "version": "$version",
          "update_link": "$link"
        }
      ]
    }
  }
}
"@
  [System.IO.File]::WriteAllText((Join-Path $root "release\updates.json"),
    $updatesJson, (New-Object System.Text.UTF8Encoding($false)))
  New-Item -ItemType Directory -Force -Path (Join-Path $root "docs") | Out-Null
  [System.IO.File]::WriteAllText((Join-Path $root "docs\updates.json"),
    $updatesJson, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "updated docs\updates.json for Firefox auto-update" -ForegroundColor Green
}

# --- done --------------------------------------------------------------------
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
