# ytgrab — remove the native messaging host
#
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$root    = Split-Path -Parent $PSScriptRoot
$hostDir = Join-Path $root "host"

$key = "HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab"
if (Test-Path $key) {
  Remove-Item -Path $key -Recurse -Force
  Write-Host "removed registry key: $key"
} else {
  Write-Host "registry key not present"
}

foreach ($file in @("ytgrab.manifest.json", "ytgrab-launcher.bat")) {
  $path = Join-Path $hostDir $file
  if (Test-Path $path) {
    Remove-Item -Path $path -Force
    Write-Host "removed $path"
  }
}

Write-Host "Done. (The built exe under host\dist and your config are untouched.)"
