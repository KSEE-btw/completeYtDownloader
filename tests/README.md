# Regression suites

Plain scripts, no test framework: each `check_*.py` imports the package from
`src/`, prints a PASS/FAIL report and exits non-zero on failure. They were kept
as scripts because most of them drive real windows (Tk) and real children
(ffmpeg, yt-dlp) rather than pure functions.

```powershell
python tests/run_all.py           # offline suites
python tests/run_all.py --net     # add the suites that download from YouTube
python tests/check_queue.py       # or run one directly
```

| suite | what it covers | needs |
| --- | --- | --- |
| `check_editor.py` | editor pack decisions: CFR, section args, frame rates, clip length | – |
| `check_clip_progress.py` | clip progress parsing: `time=`, spans, open-ended clips, ETA hold-off | – |
| `check_queue.py` | parallel queue: cap, FIFO, pause/resume, reorder, bulk cancel/remove | – |
| `check_editor_e2e.py` | the real `run_download()` + ffmpeg pipeline against a local fixture | ffmpeg |
| `check_controls.py` | queue window controls: selection, footer, bulk actions | Tk |
| `check_theme.py` | light/dark theme toggle and persistence | Tk |
| `check_editor_net.py` | same as e2e but against real YouTube clips | network, yt-dlp, ffmpeg |
| `check_clip_progress_net.py` | clip progress on a real download | network, yt-dlp, ffmpeg |

`scripts/check_versions.py` runs in CI too, but it is a build guard rather than
a suite. The frozen executables are covered by `ytgrab --smoke` and
`ytgrab --self-test`, which run against the built binaries.

The network suites download a 19 s clip (`jNQXAC9IVRw`) and Big Buck Bunny
(`aqz-KE-bpKQ`, 10:34, 1080p60), so they are excluded from the default run and
from CI. `--download-sections` can fail on live streams, and
`--cookies-from-browser firefox` is a common hang suspect.
