# ytgrab -- remove the native messaging host
#
#   from a checkout:        powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
#   from a release bundle:  powershell -ExecutionPolicy Bypass -File uninstall.ps1
#
# Options:
#   -Purge   also delete your saved settings (~/.config/ytgrab)
#   -Deep    also scrub the bookkeeping a temporary add-on leaves in your
#            Firefox profile. Firefox must be closed for this; prefs.js is
#            backed up next to itself before it is touched.
#
# The extension itself is removed from inside Firefox: a permanent install goes
# away in about:addons, a temporary one the moment Firefox restarts.

[CmdletBinding()]
param(
  [switch]$Purge,
  [switch]$Deep
)

$ErrorActionPreference = "Continue"
$here = $PSScriptRoot

# --- 1. stop a running host so its files are not still locked ---------------
$running = @(Get-Process ytgrab-host -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
  $running | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 600
  Write-Host "stopped $($running.Count) running host process(es)"
}

# --- 2. registry key --------------------------------------------------------
$key = "HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab"
if (Test-Path $key) {
  Remove-Item -Path $key -Recurse -Force
  Write-Host "removed registry key: $key"
} else {
  Write-Host "registry key not present"
}

# --- 3. installed copy ------------------------------------------------------
$installDir = Join-Path $env:LOCALAPPDATA "ytgrab"

# --- 3b. stop the console CLI too, and drop the folder from the user PATH ---
Get-Process ytgrab -ErrorAction SilentlyContinue |
  Stop-Process -Force -ErrorAction SilentlyContinue
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath) {
  try {
    $parts = @($currentPath.Split(";") |
                 Where-Object { $_ -ne "" -and $_ -ne $installDir })
    $newPath = $parts -join ";"
    if ($newPath -ne $currentPath) {
      [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
      Write-Host "removed $installDir from your user PATH"
    }
  } catch {
    Write-Warning "could not update your user PATH: $_"
  }
}

if (Test-Path $installDir) {
  Remove-Item -Path $installDir -Recurse -Force -ErrorAction SilentlyContinue
  if (Test-Path $installDir) {
    Write-Host "could not fully remove $installDir (a host process may still hold it)" -ForegroundColor Yellow
  } else {
    Write-Host "removed $installDir"
  }
} else {
  Write-Host "install folder not present"
}

# --- 4. manifests written by older versions of install.ps1 ------------------
foreach ($rel in @("host\ytgrab.manifest.json", "host\ytgrab-launcher.bat")) {
  $legacy = Join-Path (Split-Path -Parent $here) $rel
  if (Test-Path $legacy) {
    Remove-Item -Path $legacy -Force
    Write-Host "removed $legacy"
  }
}

# --- 5. saved settings ------------------------------------------------------
if ($Purge) {
  $config = Join-Path $env:USERPROFILE ".config\ytgrab"
  if (Test-Path $config) {
    Remove-Item -Path $config -Recurse -Force
    Write-Host "removed $config"
  }
}

# --- 6. leftover Firefox profile bookkeeping --------------------------------
if ($Deep) {
  if (Get-Process firefox -ErrorAction SilentlyContinue) {
    Write-Host "Firefox is running -- close it and re-run with -Deep to finish" -ForegroundColor Yellow
  } else {
    $lib = Join-Path $here "lib\firefox.ps1"
    if (-not (Test-Path $lib)) {
      Write-Host "lib\firefox.ps1 not found; skipping -Deep" -ForegroundColor Yellow
    } else {
      . $lib
      $total = 0
      foreach ($prof in Get-YtgrabFirefoxProfileDirs) {
        $n = Remove-YtgrabFirefoxPrefs -PrefsPath (Join-Path $prof.FullName "prefs.js")
        if ($n -gt 0) {
          Write-Host "scrubbed $n line(s) from $($prof.Name)\prefs.js"
          $total += $n
        }
      }
      if ($total -eq 0) { Write-Host "no completeYtDownloader traces left in any Firefox profile" }
    }
  }
}

# --- done -------------------------------------------------------------------
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "If completeYtDownloader is still listed in about:addons, remove it there; a temporary"
Write-Host "add-on goes away on its own when Firefox restarts."
