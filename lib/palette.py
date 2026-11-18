"""One status-colour palette for every output: the chart image sent with alerts (matplotlib), the local dashboard,
setup detail, lifecycle view and weekly recap (HTML). Change a colour here and every output follows.

    import palette
    palette.status_key("closed_tp2")   -> "tp"
    palette.STATUS["tp"]["fg"]          -> "#22c55e"
    palette.css()                       -> CSS variables + .p-<key> pill classes for the HTML pages
"""
from __future__ import annotations

THEME = {"bg": "#090c12", "bg2": "#0d111a", "panel": "#111723", "panel2": "#161d2b", "line": "#222b3b", "line2": "#2c3649",
         "tx": "#e8ecf3", "tx2": "#a9b2c3", "mut": "#6f7a8f", "up": "#22c55e", "down": "#f25555"}

# status key -> label, text colour, fill, border
STATUS = {
    "pending":   {"label": "PENDING",   "fg": "#4f9dff", "bg": "#132743", "border": "#1f3d66"},
    "filled":    {"label": "FILLED",    "fg": "#f5b041", "bg": "#3a2c12", "border": "#5a4318"},
    "tp":        {"label": "TP HIT",    "fg": "#22c55e", "bg": "#16351f", "border": "#235a33"},
    "stop":      {"label": "STOPPED",   "fg": "#f25555", "bg": "#3a1618", "border": "#5e2326"},
    "be":        {"label": "BREAKEVEN", "fg": "#cbd5e1", "bg": "#1e2533", "border": "#36415a"},
    "void":      {"label": "VOID",      "fg": "#9aa4b6", "bg": "#171c26", "border": "#2d3546"},
    "invalid":   {"label": "INVALID",   "fg": "#9aa4b6", "bg": "#171c26", "border": "#2d3546"},
    "cancelled": {"label": "CANCELLED", "fg": "#9aa4b6", "bg": "#171c26", "border": "#2d3546"},
    "locked":    {"label": "LOCKED",    "fg": "#a78bfa", "bg": "#261d42", "border": "#4a3a80"},
}

# chart levels / zones
LEVEL = {"entry": "#f5b041", "stop": "#f25555", "target": "#22c55e", "sweep": "#f5b041", "mss": "#e8ecf3",
         "fvg": "#a78bfa", "ote": "#2dd4bf", "unicorn": "#eab308", "pool": "#6f7a8f"}
DIRECTION = {"short": {"fg": "#ff8a8a", "bg": "#3a1618"}, "long": {"fg": "#6ee7a0", "bg": "#16351f"}}

_MAP = {"pending": "pending", "new": "pending", "filled": "filled", "tp1": "tp", "tp2": "tp", "tp3": "tp",
        "closed_tp1": "tp", "closed_tp2": "tp", "closed_tp3": "tp", "stop": "stop", "closed_stop": "stop",
        "be": "be", "closed_be": "be", "void": "void", "invalid": "invalid", "cancelled": "cancelled", "locked": "locked"}

# what the pill says for each setup status / alert event
LABELS = {"pending": "PENDING", "new": "NEW SETUP", "filled": "FILLED", "tp1": "TP1 HIT", "tp2": "TP2 HIT", "tp3": "TP3 HIT",
          "closed_tp1": "CLOSED TP1", "closed_tp2": "CLOSED TP2", "closed_tp3": "CLOSED TP3", "stop": "STOPPED",
          "closed_stop": "STOPPED", "be": "BREAKEVEN", "closed_be": "BREAKEVEN", "void": "VOID", "invalid": "INVALID",
          "cancelled": "CANCELLED", "locked": "LOCKED", "update": "UPDATE"}


def status_key(status_or_event: str | None) -> str:
    return _MAP.get((status_or_event or "pending").lower(), "pending")


def colors(status_or_event: str | None) -> dict:
    return STATUS[status_key(status_or_event)]


def label(status_or_event: str | None) -> str:
    return LABELS.get((status_or_event or "pending").lower(), (status_or_event or "").upper())


def css() -> str:
    v = ";".join(f"--{k}:{c}" for k, c in THEME.items())
    v += ";" + ";".join(f"--lv-{k}:{c}" for k, c in LEVEL.items())
    pills = "".join(f".p-{k}{{color:{s['fg']};background:{s['bg']};border-color:{s['border']}}}" for k, s in STATUS.items())
    dots = "".join(f".d-{k}{{background:{s['fg']}}}.t-{k}{{color:{s['fg']}}}" for k, s in STATUS.items())
    dirs = "".join(f".{k}{{color:{s['fg']};background:{s['bg']}}}" for k, s in DIRECTION.items())
    return f":root{{{v}}}{pills}{dots}{dirs}"
