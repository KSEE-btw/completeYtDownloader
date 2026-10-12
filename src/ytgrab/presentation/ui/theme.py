"""Breeze palettes and the Windows app-theme lookup."""

from __future__ import annotations

from ytgrab.infrastructure.config import load_config
# Breeze -- KDE Plasma's default look.  Two variants: the queue window picks one
# at start-up (an explicit ui_theme from config, else the Windows app theme) and
# the toggle in its toolbar flips between them live.
BREEZE_LIGHT = {
    "bg": "#eff0f1", "card": "#ffffff", "card_hi": "#f7f8f9",
    "header": "#fcfcfc", "border": "#c9ced3",
    "fg": "#232629", "muted": "#6f7a80",
    "accent": "#3daee9", "accent_dk": "#2980b9", "press_fg": "#ffffff",
    "ok": "#27ae60", "warn": "#f67400", "bad": "#da4453",
    "trough": "#e3e5e6",
}
BREEZE_DARK = {
    "bg": "#31363b", "card": "#232629", "card_hi": "#2c3136",
    "header": "#2a2e32", "border": "#1b1e20",
    "fg": "#eff0f1", "muted": "#9aa0a6",
    "accent": "#3daee9", "accent_dk": "#2980b9", "press_fg": "#ffffff",
    "ok": "#27ae60", "warn": "#f67400", "bad": "#da4453",
    "trough": "#1b1e20",
}
_STRIPES = {"queued": "#9aa0a6", "running": "#3daee9", "paused": "#f67400",
            "done": "#27ae60", "error": "#da4453", "cancelled": "#b8bec3"}
for _pal in (BREEZE_LIGHT, BREEZE_DARK):
    _pal["stripe"] = dict(_STRIPES)
THEMES = {"light": BREEZE_LIGHT, "dark": BREEZE_DARK}
# back-compat aliases: the light variant, used by tests and diagnostics
BG = BREEZE_LIGHT["bg"]
CARD = BREEZE_LIGHT["card"]
CARD_HI = BREEZE_LIGHT["card_hi"]
HEADER = BREEZE_LIGHT["header"]
BORDER = BREEZE_LIGHT["border"]
FG = BREEZE_LIGHT["fg"]
MUTED = BREEZE_LIGHT["muted"]
ACCENT = BREEZE_LIGHT["accent"]
ACCENT_DK = BREEZE_LIGHT["accent_dk"]
OK = BREEZE_LIGHT["ok"]
WARN = BREEZE_LIGHT["warn"]
BAD = BREEZE_LIGHT["bad"]
TROUGH = BREEZE_LIGHT["trough"]
STRIPE = BREEZE_LIGHT["stripe"]
def windows_app_theme() -> str:
    """Windows' own light/dark choice, so the queue matches the desktop."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if light else "dark"
    except Exception:
        return "light"
def config_theme() -> str:
    """ui_theme from config; "auto" follows the Windows app theme."""
    try:
        want = str(load_config().get("ui_theme") or "auto").lower()
    except Exception:
        want = "auto"
    return want if want in THEMES else windows_app_theme()
def pick_ui_font(root) -> str:
    """Prefer a KDE-ish sans if it is installed, else a clean Windows one."""
    try:
        families = set(root.tk.call("font", "families"))
    except Exception:
        return "Segoe UI"
    for name in ("Noto Sans", "Cantarell", "Segoe UI", "DejaVu Sans",
                 "Helvetica", "Arial"):
        if name in families:
            return name
    return "TkDefaultFont"
