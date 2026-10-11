# ytgrab -- package the Firefox extension, and optionally sign it with Mozilla
#
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Sign
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Sign -BumpPatch
#
# Builds the extension into web-ext-artifacts\.  With -Sign it also uploads the
# add-on to addons.mozilla.org, which returns a *signed* .xpi.  A signed .xpi is
# the only way to install permanently in release Firefox (unsigned ones only
# load temporarily through about:debugging).
#
# AMO credentials (create at https://addons.mozilla.org/developers/addon/api/key/)
# are read from $env:AMO_JWT_ISSUER / $env:AMO_JWT_SECRET, or pass -ApiKey/-ApiSecret.

[CmdletBinding()]
param(
  [switch]$Sign,
  [switch]$BumpPatch,
  [string]$ApiKey    = $env:AMO_JWT_ISSUER,
  [string]$ApiSecret = $env:AMO_JWT_SECRET,
  # web-ext 10+ requires Node 20; pin to 8 so Node 18 also works.
  [string]$WebExt    = "8"
)

$ErrorActionPreference = "Stop"

$root         = Split-Path -Parent $PSScriptRoot
$extDir       = Join-Path $root "extension"
$artifacts    = Join-Path $root "web-ext-artifacts"
$manifestPath = Join-Path $extDir "manifest.json"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "Node.js not found. Install Node 18+ from https://nodejs.org and try again."
}

# --- optionally bump the patch version ---------------------------------------
# AMO refuses to sign a version number it has already seen, so a fresh release
# needs a fresh version.
if ($BumpPatch) {
  $raw = Get-Content $manifestPath -Raw
  $cur = ($raw | ConvertFrom-Json).version
  $parts = $cur.Split(".")
  $parts[2] = ([int]$parts[2] + 1).ToString()
  $new = ($parts -join ".")
  $raw = [regex]::Replace($raw, '"version"\s*:\s*"' + [regex]::Escape($cur) + '"',
                          '"version": "' + $new + '"', 1)
  [System.IO.File]::WriteAllText($manifestPath, $raw, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "version bumped: $cur -> $new" -ForegroundColor Cyan
}

$version = (Get-Content $manifestPath -Raw | ConvertFrom-Json).version

Push-Location $root
try {
  # --- lint (the same validator AMO runs) ------------------------------------
  Write-Host ""
  Write-Host "linting extension..." -ForegroundColor Cyan
  $lint = & npx --yes "web-ext@$WebExt" lint --source-dir "$extDir" 2>&1
  $lint | Write-Host
  if (($lint -join "`n") -match "errors\s+[1-9]") {
    throw "web-ext lint found errors -- fix them before packaging"
  }

  # --- build -----------------------------------------------------------------
  Write-Host ""
  Write-Host "building extension $version..." -ForegroundColor Cyan
  & npx --yes "web-ext@$WebExt" build --source-dir "$extDir" `
      --artifacts-dir "$artifacts" --overwrite-dest
  if ($LASTEXITCODE -ne 0) { throw "web-ext build failed" }

  $zip = Get-ChildItem (Join-Path $artifacts "*.zip") |
           Sort-Object LastWriteTime | Select-Object -Last 1
  $unsigned = Join-Path $artifacts "ytgrab-$version-unsigned.xpi"
  Copy-Item $zip.FullName $unsigned -Force
  Write-Host "unsigned package: $unsigned" -ForegroundColor Green
  Write-Host "  (load temporarily via about:debugging; it will NOT install permanently)"

  # --- sign ------------------------------------------------------------------
  if (-not $Sign) {
    Write-Host ""
    Write-Host "To get a permanent, installable .xpi add -Sign." -ForegroundColor DarkGray
    return
  }

  if (-not $ApiKey -or -not $ApiSecret) {
    throw @"
Signing needs addons.mozilla.org credentials.
  * create them at https://addons.mozilla.org/developers/addon/api/key/
  * then either export them:
        `$env:AMO_JWT_ISSUER  = 'user:12345:67'
        `$env:AMO_JWT_SECRET  = 'deadbeef...'
    or pass -ApiKey / -ApiSecret.
"@
  }

  Write-Host ""
  Write-Host "submitting to addons.mozilla.org for signing (channel: unlisted)..." -ForegroundColor Cyan
  Write-Host "this can take anywhere from a few seconds to ~24h if it gets flagged for review."
  & npx --yes "web-ext@$WebExt" sign --source-dir "$extDir" `
      --artifacts-dir "$artifacts" --channel unlisted `
      --api-key "$ApiKey" --api-secret "$ApiSecret"
  if ($LASTEXITCODE -ne 0) { throw "web-ext sign failed" }

  $signed = Get-ChildItem (Join-Path $artifacts "*.xpi") |
              Where-Object { $_.Name -ne (Split-Path $unsigned -Leaf) } |
              Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $signed) { throw "signing reported success but no .xpi was downloaded" }

  Write-Host ""
  Write-Host "signed package : $($signed.FullName)" -ForegroundColor Green
  Write-Host "Install it:     about:addons -> gear -> 'Install Add-on From File...'"
  Write-Host "                (or just drag the .xpi into a Firefox window)"
} finally {
  Pop-Location
}
