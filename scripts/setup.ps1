# ytgrab -- interactive installer for new users
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Walks you through four opt-in steps and does the heavy lifting:
#   1. yt-dlp    -- what actually downloads the video
#   2. ffmpeg    -- what converts it when a conversion is needed
#   3. the ytgrab helper   (the small local program Firefox talks to)
#   4. the browser extension, installed permanently
#
# Nothing needs administrator rights. Every step can be skipped, and running
# it again is safe -- anything already installed is detected and left alone.
#
# Unattended use:
#   setup.ps1 -Yes                      answer yes to everything
#   setup.ps1 -Yes -Browser firefox     skip the browser question

[CmdletBinding()]
param(
  [switch]$Yes,
  [string]$Browser = "",
  [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

# --------------------------------------------------------------------------- #
# console helpers
# --------------------------------------------------------------------------- #
function Write-Head([string]$t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Write-Ok([string]$t)   { Write-Host "   [ok] $t" -ForegroundColor Green }
function Write-Info([string]$t) { Write-Host "   $t" }
function Write-Warn2([string]$t){ Write-Host "   [!]  $t" -ForegroundColor Yellow }
function Write-Bad([string]$t)  { Write-Host "   [x]  $t" -ForegroundColor Red }
function Write-Dim([string]$t)  { Write-Host "   $t" -ForegroundColor DarkGray }

function Read-YesNo([string]$question, [bool]$default = $true) {
  if ($Yes) { return $default }
  $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
  while ($true) {
    Write-Host ""
    Write-Host "   $question $hint " -NoNewline
    $a = Read-Host
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    switch ($a.Trim().ToLower()) {
      "y"   { return $true }
      "yes" { return $true }
      "n"   { return $false }
      "no"  { return $false }
      default { Write-Dim "please answer y or n" }
    }
  }
}

function Pause-ForUser([string]$message = "Press Enter to continue") {
  if ($Yes) { return }
  Write-Host ""
  Write-Host "   $message " -NoNewline
  $null = Read-Host
}

# --------------------------------------------------------------------------- #
# tool + browser discovery
# --------------------------------------------------------------------------- #
$wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
$haveWinget  = [bool](Get-Command winget -ErrorAction SilentlyContinue)

function Update-SessionPath {
  # a fresh winget install updates the registry PATH, not this process
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user    = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Find-Tool([string]$name) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $direct = Join-Path $wingetLinks "$name.exe"
  if (Test-Path $direct) { return $direct }
  return $null
}

function Get-Browsers {
  $defs = @(
    [pscustomobject]@{ Name="Firefox"; Version=""; Kind="firefox";
      Exe=@("$env:ProgramFiles\Mozilla Firefox\firefox.exe", "${env:ProgramFiles(x86)}\Mozilla Firefox\firefox.exe");
      HostKey="HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Firefox Developer Edition"; Version=""; Kind="firefox";
      Exe=@("$env:ProgramFiles\Firefox Developer Edition\firefox.exe");
      HostKey="HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Google Chrome"; Version=""; Kind="chromium";
      Exe=@("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe");
      HostKey="HKCU:\Software\Google\Chrome\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Microsoft Edge"; Version=""; Kind="chromium";
      Exe=@("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe");
      HostKey="HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Brave"; Version=""; Kind="chromium";
      Exe=@("$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe");
      HostKey="HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Vivaldi"; Version=""; Kind="chromium";
      Exe=@("$env:LOCALAPPDATA\Vivaldi\Application\vivaldi.exe");
      HostKey="HKCU:\Software\Vivaldi\NativeMessagingHosts\ytgrab" }
    [pscustomobject]@{ Name="Opera"; Version=""; Kind="chromium";
      Exe=@("$env:LOCALAPPDATA\Programs\Opera\opera.exe");
      HostKey="HKCU:\Software\Opera Software\Opera Stable\NativeMessagingHosts\ytgrab" }
  )
  $found = @()
  foreach ($d in $defs) {
    foreach ($p in $d.Exe) {
      if (Test-Path $p) {
        $found += [pscustomobject]@{
          Name    = $d.Name
          Path    = $p
          Kind    = $d.Kind
          HostKey = $d.HostKey
          Version = (Get-Item $p).VersionInfo.ProductVersion
        }
        break
      }
    }
  }
  return $found
}

function Find-SignedXpi {
  $dirs = @(
    $here,
    (Join-Path $here "web-ext-artifacts"),
    (Join-Path $here "..\web-ext-artifacts")
  )
  $all = @()
  foreach ($d in $dirs) {
    if (Test-Path $d) {
      $all += Get-ChildItem (Join-Path $d "*.xpi") -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notlike "*unsigned*" }
    }
  }
  if (-not $all) { return $null }
  return ($all | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}

function Test-FirefoxExtensionSeen {
  # has Firefox recorded ytgrab in any profile's add-on database?
  $profiles = Join-Path $env:APPDATA "Mozilla\Firefox\Profiles"
  if (-not (Test-Path $profiles)) { return $false }
  foreach ($p in Get-ChildItem $profiles -Directory -ErrorAction SilentlyContinue) {
    $db = Join-Path $p.FullName "extensions.json"
    if ((Test-Path $db) -and ((Get-Content $db -Raw) -match "ytgrab@kunal.local")) {
      return $true
    }
  }
  return $false
}

# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
$state = [ordered]@{ ytdlp = "skipped"; ffmpeg = "skipped"; host = "skipped"; extension = "skipped" }

function Step-Tool([string]$label, [string]$probe, [string]$wingetId, [string]$why, [string]$key) {
  Write-Head "$label"
  $found = Find-Tool $probe
  if ($found) {
    Write-Ok "already installed ($found)"
    $state[$key] = "already installed"
    return
  }
  Write-Info "not found on this computer."
  Write-Info $why
  $go = Read-YesNo "Download and install $label now?"
  if (-not $go) { Write-Warn2 "skipped -- the helper will not work without it"; return }
  if (-not $haveWinget) {
    Write-Bad "winget is not available on this PC"
    Write-Info "install $label from its website, then run this script again"
    return
  }
  Write-Dim "winget install --id $wingetId -e"
  & winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements | Out-Host
  Update-SessionPath
  $found = Find-Tool $probe
  if ($found) {
    Write-Ok "installed ($found)"
    $state[$key] = "installed"
  } else {
    Write-Warn2 "winget finished but $label is not on PATH yet -- a new Firefox start will see it"
    $state[$key] = "installed (restart Firefox to pick it up)"
  }
}

function Step-Host {
  Write-Head "ytgrab helper"
  Write-Info "the small local program the extension talks to"
  $script = Join-Path $here "install.ps1"
  if (-not (Test-Path $script)) {
    Write-Bad "install.ps1 not found next to setup.ps1"
    return
  }
  try {
    & $script -SkipDeps -Quiet
    Write-Ok "helper installed and registered"
    $state.host = "installed"
  } catch {
    Write-Bad "helper install failed: $_"
  }
}

function Step-Browser {
  Write-Head "browser extension"

  $browsers = Get-Browsers
  if (-not $browsers) {
    Write-Bad "no supported browser found"
    Write-Info "install Firefox (https://www.mozilla.org/firefox/) and run this again"
    return
  }

  Write-Info "browsers detected on this PC:"
  for ($i = 0; $i -lt $browsers.Count; $i++) {
    $b = $browsers[$i]
    $tag = if ($b.Kind -eq "firefox") { "supported" } else { "not supported (Firefox-only add-on)" }
    Write-Host ("     [{0}] {1} {2}  -- {3}" -f ($i + 1), $b.Name, $b.Version, $tag)
  }

  # pick one
  $choice = $null
  if ($Browser) {
    if ($Browser -eq "none") { Write-Warn2 "skipped"; return }
    $choice = $browsers | Where-Object { $_.Name -like "*$Browser*" } | Select-Object -First 1
    if (-not $choice) { Write-Warn2 "no browser matching '$Browser' found"; return }
  } elseif ($Yes) {
    $choice = $browsers | Where-Object { $_.Kind -eq "firefox" } | Select-Object -First 1
    if (-not $choice) { Write-Warn2 "no Firefox found"; return }
  } else {
    Write-Host ""
    Write-Host "   Install the ytgrab extension into which browser? [1] " -NoNewline
    $a = Read-Host
    $n = 1
    if (-not [string]::IsNullOrWhiteSpace($a) -and -not [int]::TryParse($a, [ref]$n)) { $n = 1 }
    if ($n -lt 1 -or $n -gt $browsers.Count) { $n = 1 }
    $choice = $browsers[$n - 1]
  }

  Write-Host ""
  if ($choice.Kind -eq "chromium") {
    Write-Warn2 "$($choice.Name): ytgrab is a Firefox add-on only."
    Write-Info "That is deliberate, not an oversight. Chromium browsers need their own"
    Write-Info "build (service-worker background, chrome.* APIs, a pinned extension id,"
    Write-Info "PNG icons) and, with no store listing, a policy or Developer Mode"
    Write-Info "install -- a much worse experience than clicking Add in Firefox."
    Write-Info "Use Firefox: the helper you just installed already talks to it."
    $state.extension = "Firefox only (skipped $($choice.Name))"
    return
  }

  if (Test-FirefoxExtensionSeen) {
    Write-Ok "ytgrab already has an install record in Firefox"
    Write-Dim "if it does not appear in about:addons, continue below"
  }

  $xpi = Find-SignedXpi
  if (-not $xpi) {
    Write-Warn2 "no Mozilla-signed .xpi found next to this installer"
    Write-Info "Firefox only keeps an add-on permanently if Mozilla signed it."
    Write-Info "Build one once (free):  scripts\package.ps1 -Sign"
    Write-Info "or load it temporarily for now:"
    Write-Info "  about:debugging#/runtime/this-firefox -> Load Temporary Add-on"
    $state.extension = "needs a signed .xpi"
    return
  }

  Write-Ok "signed package: $xpi"
  $go = Read-YesNo "Open it in $($choice.Name) now to install?"
  if (-not $go) { Write-Warn2 "skipped"; return }

  Start-Process -FilePath $choice.Path -ArgumentList @("`"$xpi`"")
  Write-Info "Firefox should ask you to add ytgrab -- click 'Add'."
  Pause-ForUser "Press Enter once you have clicked 'Add'"
  $state.extension = "install prompted"
}

# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "  ytgrab installer" -ForegroundColor Cyan
Write-Host "  YouTube -> Premiere downloads, right in your browser"
Write-Host ""
Write-Info "This sets up four things, in order, and you can skip any of them:"
Write-Info "  1. yt-dlp   (downloads the video)"
Write-Info "  2. ffmpeg   (converts it only when the codecs require it)"
Write-Info "  3. the ytgrab helper (the local program the browser talks to)"
Write-Info "  4. the browser extension"
Write-Host ""
Write-Info "No administrator rights are needed."
Pause-ForUser "Press Enter to begin"

if ($SkipDeps) {
  Write-Head "external tools"
  Write-Warn2 "skipping yt-dlp and ffmpeg (-SkipDeps)"
  $state.ytdlp  = "skipped (-SkipDeps)"
  $state.ffmpeg = "skipped (-SkipDeps)"
} else {
  Step-Tool "yt-dlp" "yt-dlp" "yt-dlp.yt-dlp" `
    "yt-dlp is what actually downloads the video." "ytdlp"
  Step-Tool "ffmpeg" "ffmpeg" "Gyan.FFmpeg" `
    "ffmpeg converts the download when the codecs are not Premiere-friendly." "ffmpeg"
}

Step-Host
Step-Browser

# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "== summary" -ForegroundColor Cyan
Write-Host ("   yt-dlp            : {0}" -f $state.ytdlp)
Write-Host ("   ffmpeg            : {0}" -f $state.ffmpeg)
Write-Host ("   ytgrab helper     : {0}" -f $state.host)
Write-Host ("   browser extension : {0}" -f $state.extension)
Write-Host ""

if ($state.extension -like "install prompted" -or (Test-FirefoxExtensionSeen)) {
  Write-Ok "Open any YouTube video and click the blue arrow at the bottom right."
} else {
  Write-Info "Next: get a signed .xpi (scripts\package.ps1 -Sign) and install it,"
  Write-Info "or load the extension temporarily from about:debugging."
}
Write-Host ""
