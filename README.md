# ytgrab — YouTube → Premiere downloader for Firefox

A tiny Firefox extension that puts a **yt-dlp format picker right on the
page**, downloads whatever you choose, and **converts to a Premiere-compatible
file only when the codecs actually require it**.

At 1080p YouTube serves H.264 + AAC in an MP4, so nothing is converted.
Pick a VP9/AV1 stream (or 1440p/4K, which only exist as VP9/AV1) and the host
re-encodes it to H.264/AAC on the way to disk.

```
┌────────────────────── Firefox ──────────────────────┐
│  content script  ─┐                                  │
│  toolbar popup   ─┼─▶ background broker ─ native ───▶ │  ytgrab-host.exe
└───────────────────┘      (ports)       messaging     └──────┬───────
                                                              │
                                                    yt-dlp ───┴─── ffmpeg
```

A browser extension can't execute `yt-dlp` itself, so the extension talks to a
small local companion (a single ~6 MB Python executable) over Firefox's
[Native messaging](https://developer.mozilla.org/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)
channel. The companion is what actually downloads, probes and converts.

## How the "convert only when needed" logic works

1. **Format list.** The host runs `yt-dlp -J` and classifies every stream:
   - `ok` — H.264/HEVC video + AAC/MP3/PCM audio in MP4/MOV → Premiere imports it directly
   - `repack` — right codecs, wrong container (e.g. H.264 in MKV) → remuxed in seconds, no re-encode
   - `convert` — VP9 / AV1 / Opus / Vorbis → real re-encode
2. **Download.** With the *Auto — best Premiere-safe* entry selected, the host
   downloads the best **H.264 + AAC** stream YouTube offers, so the common
   1080p case never converts.
3. **Verify, then convert.** After the download the host runs `ffprobe` on the
   *actual file* and only converts if the codecs aren't Premiere-friendly.
   The checkbox just says "if needed".

Conversion targets: **H.264/AAC MP4** (default), **DNxHR HQ/PCM MOV**,
**ProRes 422 HQ/PCM MOV**.

## Requirements

- Windows 10/11, Firefox 115+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp), `ffmpeg` and `ffprobe` on `PATH`
  (`winget install yt-dlp.yt-dlp Gyan.FFmpeg`)
- Python 3.9+ — only to install/build the companion; the shipped exe is standalone
- Node 18+ — only to package/sign the extension (`web-ext`)

## Install

### 1. The companion host (once per machine)

```powershell
cd ytgrab
# builds host\dist\ytgrab-host.exe, installs it and registers it with Firefox
powershell -ExecutionPolicy Bypass -File scripts\build_host.ps1
```

The installer copies the host to `%LOCALAPPDATA%\ytgrab`, writes the
native-messaging manifest there, and adds
`HKCU\Software\Mozilla\NativeMessagingHosts\ytgrab` — **no admin rights needed**,
and it keeps working if you later move or delete this checkout. Missing
`yt-dlp`/`ffmpeg`? Add `-InstallDeps` and the installer runs `winget` for you:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -InstallDeps
```

(No exe builder handy? Plain `scripts\install.ps1` falls back to a `pythonw`
launcher, so the host still runs straight from the Python source.)

### 2. The extension

For a quick trial (this vanishes the moment Firefox quits):

1. Open `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…** → pick `extension/manifest.json`
3. Open any YouTube video — click the blue arrow button at the bottom right.

For a permanent install, sign it once — see
[Make it permanent](#make-it-permanent-so-it-survives-a-firefox-restart).

## Using it

- The floating button appears on YouTube pages; the toolbar icon works anywhere
  (paste any URL yt-dlp understands, not just YouTube).
- Pick a format — each entry is tagged `Premiere ✓`, `repack` or `convert`.
- Leave **"Convert to a Premiere-compatible file — only if needed"** ticked
  and choose a target codec.
- **Download** → progress bar → **Show file** opens Explorer with the file selected.

## Configuration

`~/.config/ytgrab/config.json` (created on first write; see
`config.example.json`):

| key | meaning |
| --- | --- |
| `outdir` | where finished files go |
| `default_mode` | `premiere_safe` or `best` for the Auto entry |
| `default_target` | `h264`, `dnxhr` or `prores` |
| `keep_original` | keep the non-Premiere source after converting |
| `cookies_from_browser` | e.g. `firefox` for age-restricted / member videos |
| `force_no_playlist` | refuse playlist URLs (default `true`) |
| `extra_ytdlp_args` | extra raw yt-dlp flags |
| `ytdlp` / `ffmpeg` / `ffprobe` / `aria2c` | explicit binary paths (default: find on PATH) |

## Speed

Everything below is **quality-neutral** by default — none of it changes the
pixels that come out.

| setting | default | what it does |
| --- | --- | --- |
| `concurrent_fragments` | `8` | download DASH fragments in parallel (`-N`); the biggest win on segmented sites like YouTube |
| `use_aria2c` | `auto` | if [aria2c](https://aria2.github.io/) is on PATH, use 16 connections per file |
| `http_chunk_size` | `""` | split HTTP ranges to beat throttling (e.g. `10M`); off by default |
| `hwaccel` | `true` | decode with the GPU; falls back to CPU automatically if the codec isn't supported |
| `x264_preset` | `faster` | x264 speed/size trade-off. At a fixed CRF the preset does **not** change quality, only encoding time and file size |
| `x264_crf` | `18` | quality target (lower = better/bigger). `18` is visually lossless |
| `video_encoder` | `libx264` | `h264_nvenc` / `h264_qsv` / `h264_amf` are much faster but slightly less efficient per bitrate — opt in only if you want the speed |

Two more things the host does automatically:

- **No double copy.** The download lands in a scratch folder *inside* the
  output directory, so publishing the finished file is a rename, not a second
  full write. It also means a 4K download never needs twice the disk space.
- **Audio is stream-copied, not re-encoded**, whenever the source audio is
  already AAC/MP3 and the target is MP4. Instant, and no generation loss.

The real fastest path is to not convert at all: the *Auto — best Premiere-safe*
entry picks H.264/AAC, which almost every 1080p video offers.


## Development

```powershell
python host\ytgrab_host.py --ping                  # tools + config
python host\ytgrab_host.py --list "URL"            # raw format JSON
python scripts\show_formats.py "URL"               # pretty format table
python scripts\test_host.py "URL"                  # talk the native protocol
```

`scripts/test_host.py` speaks the real native-messaging framing, so it exercises
exactly what Firefox does without opening a browser.

## Make it permanent (so it survives a Firefox restart)

Extensions loaded from `about:debugging` are **temporary** and are gone as soon
as Firefox closes. Release Firefox will only keep an add-on permanently if
**Mozilla has signed it** — that is a Firefox security rule, not a ytgrab
limitation. Signing is free and does not publish anything unless you ask it to.

**Once per machine**, grab API credentials from
<https://addons.mozilla.org/developers/addon/api/key/> (they look like
`user:12345:67` and a long hex string), then:

```powershell
$env:AMO_JWT_ISSUER = 'user:12345:67'
$env:AMO_JWT_SECRET = 'deadbeef…'

powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Sign
```

`package.ps1` lints the add-on with the same validator AMO uses, builds it, and
uploads it for **unlisted** (self-distribution) signing. It writes a signed
`web-ext-artifacts\ytgrab-<version>.xpi`. Install that file and you're done:

- **about:addons** → gear icon (top right) → **Install Add-on From File…** →
  pick the `.xpi`, **or**
- drag the `.xpi` straight into a Firefox window and confirm.

From then on it loads automatically at every start, like any other add-on.

> AMO refuses to sign a version number it has already seen, so pass `-BumpPatch`
> (`scripts\package.ps1 -Sign -BumpPatch`) when re-releasing.

<details>
<summary>Prefer not to sign? (Developer Edition / Nightly / ESR only)</summary>

On **Firefox Developer Edition, Nightly or ESR** you can run an unsigned build
permanently: set `xpinstall.signatures.required` to `false` in `about:config`,
then install `web-ext-artifacts\ytgrab-<version>-unsigned.xpi`. The preference
is **ignored on regular Firefox**, so this is not an option there.

</details>

## Share it with someone else

One command produces a self-contained bundle:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Sign
```

That writes `release\ytgrab-<version>-windows.zip` containing the standalone
host exe, `install.ps1` / `uninstall.ps1`, the signed `.xpi`, and a
`READ-ME-FIRST.txt`. Hand the zip to anyone (attach it to a GitHub Release);
their steps are:

1. unzip it
2. `powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDeps`
   *(no admin rights; `-InstallDeps` fetches yt-dlp + ffmpeg via `winget`)*
3. drag the `.xpi` into Firefox
4. click the blue arrow on any YouTube video

The repo also ships `.github/workflows/release.yml`, which runs exactly this on a
`v*` tag and attaches the zip to a GitHub Release. Add `AMO_JWT_ISSUER` and
`AMO_JWT_SECRET` as repository secrets and the CI build is signed too.

Two things are worth knowing when you hand this out:

- **Everyone shares one extension ID.** The `.xpi` you sign is
  `ytgrab@kunal.local` (from `extension/manifest.json`), and the installer
  allows exactly that ID. Re-sign the same ID for updates — don't change it, or
  installed copies stop talking to the host.
- **The native host can't be avoided.** A browser extension is not allowed to
  run `yt-dlp`, so a local companion + a registry entry are required. That is
  why install is a two-step (helper, then extension) rather than a single
  "Add to Firefox" click.

Want it discoverable and one-click? Publish it publicly instead: set
`sign.channel` to `listed` in `.web-ext-config.mjs` and run
`npx web-ext sign --channel listed` (needs a first-listing metadata file — see
the [web-ext docs](https://extensionworkshop.com/documentation/develop/getting-started-with-web-ext/)).
The host installer still has to be distributed alongside it.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
```

Removes the registry key and the copy under `%LOCALAPPDATA%\ytgrab`. Add
`-Purge` to also delete your saved settings (`~/.config/ytgrab`). Remove the
extension itself from `about:addons` in Firefox.

## Troubleshooting

- **Badge says "host offline"** — re-run `scripts\install.ps1`, restart Firefox.
  Check the key exists:
  `Get-ItemProperty HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab`
- **"tools missing"** — `yt-dlp`/`ffmpeg` aren't on the `PATH` Firefox sees.
  `scripts\install.ps1 -InstallDeps`, or set explicit paths in the config.
- **Nothing appears on YouTube** — the extension must be loaded in
  `about:debugging` (or permanently installed); check the browser console for a
  ytgrab error.
- **Firefox refuses to install the `.xpi`** ("could not be installed because it
  is not verified") — that's the unsigned build. Sign it
  (`scripts\package.ps1 -Sign`) or switch to Firefox Developer Edition.
- **A signed install won't update** — bump the version first:
  `scripts\package.ps1 -Sign -BumpPatch`.
- **`web-ext` complains about Node** — this repo pins `web-ext@8` (Node 18+).
  web-ext 10 needs Node 20; upgrade Node or keep the pin.
