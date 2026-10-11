# Firefox profile helpers, shared by uninstall.ps1.
#
#   . (Join-Path $PSScriptRoot "lib\firefox.ps1")
#
# These only read (and, for one of them, rewrite) prefs.js inside the profile
# folders -- no registry, no network. A temporary add-on never reaches
# extensions.json, so prefs.js is where its bookkeeping actually lives.

$script:YtgrabExtId   = "ytgrab@kunal.local"
$script:YtgrabWidget  = "ytgrab_kunal_local-browser-action"

function Get-YtgrabFirefoxProfileDirs {
  $root = Join-Path $env:APPDATA "Mozilla\Firefox\Profiles"
  if (-not (Test-Path $root)) { return @() }
  return @(Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)
}

function Test-YtgrabFirefoxPrefs {
  param([Parameter(Mandatory)][string]$PrefsPath)
  if (-not (Test-Path $PrefsPath)) { return $false }
  $text = [System.IO.File]::ReadAllText($PrefsPath)
  return ($text -match [regex]::Escape($script:YtgrabExtId))
}

function Remove-YtgrabFirefoxPrefs {
  # Scrub the traces a temporary add-on leaves in prefs.js and report how many
  # lines changed. Rewriting is deliberately conservative: a line is only kept
  # in its edited form if it still looks like a complete user_pref(...)
  # statement, otherwise the original line is put back untouched.
  param([Parameter(Mandatory)][string]$PrefsPath)

  if (-not (Test-Path $PrefsPath)) { return 0 }

  $id     = [regex]::Escape($script:YtgrabExtId)
  $widget = [regex]::Escape($script:YtgrabWidget)
  $lines  = [System.IO.File]::ReadAllLines($PrefsPath)
  $out    = New-Object System.Collections.Generic.List[string]
  $changed = 0

  foreach ($line in $lines) {
    $new = $line

    if ($line -match 'devtools\.aboutdebugging\.tmpExtDirPath' -and $line -match 'ytgrab') {
      $new = ''
    } elseif ($line -match ('ExtensionStorageIDB\.migrated\.' + $id)) {
      $new = ''
    } else {
      # "ytgrab@kunal.local":"<uuid>" inside extensions.webextensions.uuids,
      # and the toolbar widget id inside browser.uiCustomization.state.
      # Quotes are backslash-escaped because these live inside a JS string.
      $new = $new -replace (',\\"' + $id + '\\":\\"[0-9a-f\-]+\\"'),   ''
      $new = $new -replace ('\\"' + $id + '\\":\\"[0-9a-f\-]+\\",'),   ''
      $new = $new -replace (',\\"' + $widget + '\\"'),                 ''
      $new = $new -replace ('\\"' + $widget + '\\",'),                 ''
    }

    if ($new -eq $line)        { $out.Add($line); continue }
    if ($new -eq '')           { $changed++;       continue }
    if ($new -match '^\s*user_pref\(' -and $new -match '\);\s*$') {
      $out.Add($new); $changed++
    } else {
      $out.Add($line)   # refuse to write something Firefox might choke on
    }
  }

  if ($changed -gt 0) {
    $backup = "$PrefsPath.ytgrab-backup"
    Copy-Item $PrefsPath $backup -Force
    [System.IO.File]::WriteAllLines($PrefsPath, $out, (New-Object System.Text.UTF8Encoding($false)))

    # Verify the rewrite before trusting it: nothing may be left behind and no
    # line may be malformed. Only then is the safety copy redundant.
    $saved   = [System.IO.File]::ReadAllLines($PrefsPath)
    $broken  = @($saved | Where-Object {
                 $_.Trim() -ne '' -and
                 -not $_.Trim().StartsWith('//') -and
                 -not ($_ -match '^\s*user_pref\(' -and $_ -match '\);\s*$') })
    $leftover = [System.IO.File]::ReadAllText($PrefsPath) -match 'ytgrab'

    if ($broken.Count -eq 0 -and -not $leftover) {
      Remove-Item $backup -Force -ErrorAction SilentlyContinue
    } else {
      Write-Warning "prefs.js did not verify clean; safety copy kept at $backup"
    }
  }
  return $changed
}

function Find-YtgrabSignedXpi {
  # A Mozilla-signed package near the invoking script: the release bundle root,
  # or web-ext-artifacts in a checkout. Unsigned builds are skipped on purpose,
  # because only a signed .xpi installs permanently in release Firefox.
  param([Parameter(Mandatory)][string]$StartDir)

  $found = @()
  foreach ($d in @($StartDir,
                   (Join-Path $StartDir "web-ext-artifacts"),
                   (Join-Path $StartDir "..\web-ext-artifacts"))) {
    if (Test-Path $d) {
      $found += Get-ChildItem (Join-Path $d "*.xpi") -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -notlike "*unsigned*" }
    }
  }
  if ($found.Count -eq 0) { return $null }
  return ($found | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}
