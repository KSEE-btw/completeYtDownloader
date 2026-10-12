import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# --- ytgrab package (layered) ---
import ytgrab.infrastructure.config as config
import ytgrab.domain.models as models
import ytgrab.services.queue as queue
import ytgrab.presentation.ui.theme as theme
import ytgrab.presentation.ui.window as window

tmp = tempfile.mkdtemp(prefix="ytgrab-theme-")
config.CONFIG_DIR = tmp
config.CONFIG_PATH = os.path.join(tmp, "config.json")
config.reset_config_cache()
with open(config.CONFIG_PATH, "w", encoding="utf-8") as fh:
    json.dump({"outdir": tmp}, fh)

from tkinter import ttk  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")


def label_for(mode):
    return "\u263e Dark" if mode == "light" else "\u2600 Light"


print("=== theme resolution ===")
check("no choice -> auto picks OS", theme.config_theme() in ("light", "dark"), True)
check("auto follows the Windows app theme",
      theme.config_theme(), theme.windows_app_theme())
config.save_config({"ui_theme": "dark"})
check("explicit dark honoured", theme.config_theme(), "dark")
config.save_config({"ui_theme": "light"})
check("explicit light honoured", theme.config_theme(), "light")
config.save_config({"ui_theme": "auto"})

qm = queue.QueueManager(os.path.join(tmp, "queue.json"))
qm.request_stop()
for _worker in qm._workers:
    _worker.join(timeout=2)
item = models.Item("r1", {"url": "https://example.com/v", "title": "A running job"})
item.status = "running"
item.stage = "downloading"
item.percent = 50.0
qm.items["r1"] = item

root = window.QueueWindow(qm, show=False)
root.withdraw()
root.update()
root._tick(force=True)

mode0 = root.mode
other = "light" if mode0 == "dark" else "dark"
print(f"=== starts in {mode0} (auto), toggles to {other} ===")
check("initial palette matches mode", root.p, theme.THEMES[mode0])
check("initial window bg", root.cget("bg"), theme.THEMES[mode0]["bg"])
check("toggle offers the other mode", root.themeBtn.cget("text"),
      label_for(mode0))

print(f"=== flip to {other} ===")
root._toggle_theme()
root.update()
pt = theme.THEMES[other]
check("mode flipped", root.mode, other)
check("palette swapped", root.p, pt)
check("window bg", root.cget("bg"), pt["bg"])
check("canvas bg", root.canvas.cget("bg"), pt["bg"])
check("card bg", root.rows["r1"]["frame"].cget("bg"), pt["card"])
check("card title fg", root.rows["r1"]["title"].cget("fg"), pt["fg"])
check("detail fg", root.rows["r1"]["detail"].cget("fg"), pt["muted"])
check("running stripe still accent", root.rows["r1"]["stripe"].cget("bg"),
      "#3daee9")
check("rows rebuilt", len(root.rows), 1)
check("footer confirms", f"{other} mode" in root.footer.cget("text"), True)
check("button offers the way back", root.themeBtn.cget("text"),
      label_for(other))
style = ttk.Style(root)
check("ttk button bg", style.lookup("Yt.TButton", "background"), pt["card"])
check("ttk button fg", style.lookup("Yt.TButton", "foreground"), pt["fg"])
check("ttk progress trough", style.lookup("Yt.Horizontal.TProgressbar",
                                          "troughcolor"), pt["trough"])
check("ttk scrollbar trough", style.lookup("Yt.Vertical.TScrollbar",
                                           "troughcolor"), pt["bg"])
check("saved to config object", config.load_config().get("ui_theme"), other)
check("saved to config file", json.load(
    open(config.CONFIG_PATH, encoding="utf-8")).get("ui_theme"), other)

print(f"=== flip back to {mode0} ===")
root._toggle_theme()
root.update()
pt0 = theme.THEMES[mode0]
check("mode back", root.mode, mode0)
check("window bg back", root.cget("bg"), pt0["bg"])
check("card bg back", root.rows["r1"]["frame"].cget("bg"), pt0["card"])
check("saved to config", config.load_config().get("ui_theme"), mode0)
check("outdir survives the theme save",
      config.load_config().get("outdir"), tmp)
check("button label back", root.themeBtn.cget("text"), label_for(mode0))

print("=== a fresh window remembers the saved choice ===")
root.destroy()
root2 = window.QueueWindow(qm, show=False)
root2.withdraw()
root2.update()
check("new window opens in saved mode", root2.mode, mode0)
root2.destroy()
qm.shutdown()

print()
print("RESULT:", "ALL PASS" if not fails else f"FAILURES: {fails}")
sys.exit(1 if fails else 0)