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
  (`winget install yt-dlp ffmpeg`)
- Python 3.9+ — only to install/build the companion; the shipped exe is standalone

## Install

```powershell
cd ytgrab
# builds host\dist\ytgrab-host.exe and registers it with Firefox
powershell -ExecutionPolicy Bypass -File scripts\build_host.ps1
```

That writes `host\ytgrab.manifest.json` and adds
`HKCU\Software\Mozilla\NativeMessagingHosts\ytgrab` — **no admin rights needed**.
(If you'd rather not build an exe, run `scripts\install.ps1` on its own; it
falls back to a `pythonw` launcher.)

Then load the extension:

1. Open `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…** → pick `extension/manifest.json`
3. Open any YouTube video — click the blue arrow button at the bottom right.

Temporary add-ons disappear when Firefox restarts. To make it permanent,
package and sign it (see below).

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

## Packaging for a permanent install

```bash
npx web-ext build --source-dir extension --artifacts-dir web-ext-artifacts
npx web-ext sign --source-dir extension --api-key ... --api-secret ...
```

(`web-ext` refuses to sign unless the extension ID in `manifest.json` matches
one you control on addons.mozilla.org. Self-distribution / unlisted signing is
the usual route.)

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
```

Removes the registry key and generated manifest. The config file and built exe
are left alone.

## Troubleshooting

- **Badge says "host offline"** — re-run `scripts\install.ps1`, restart Firefox.
  Check the key exists:
  `Get-ItemProperty HKCU:\Software\Mozilla\NativeMessagingHosts\ytgrab`
- **"tools missing"** — `yt-dlp`/`ffmpeg` aren't on the `PATH` Firefox sees.
  Set explicit paths in the config.
- **Nothing appears on YouTube** — the extension must be loaded in
  `about:debugging`; check the browser console for a ytgrab error.
