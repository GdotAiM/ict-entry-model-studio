#!/usr/bin/env python3
"""Alert chart image: the whole alert readable from the picture alone (phone notification first).

Header (instrument, direction, model, status pill in the shared palette), kill zone + countdown bar, marked-up 1m
candles (sweep, MSS, FVG, OTE, entry, stop, targets with right-edge price tags), a levels row (entry / stop / TP1 / TP2
with R), risk chips (risk %, stop points, bias label, data source) and the disclaimer. Colours come from lib/palette.py.
Only bars that have closed by `now` are drawn (no look-ahead in a replay). matplotlib only (Agg, no display).

    python3 .claude/skills/chart-markup/scripts/render_chart.py --setup state/setups/N6.json \
        --bars drafts/bars/US100_1m.csv --out charts/us100_N6_1111_new.png [--event NEW] [--now ISO] [--last 120]
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
import palette  # noqa: E402

SAST, NY = ZoneInfo("Africa/Johannesburg"), ZoneInfo("America/New_York")
T, P = palette.THEME, palette.LEVEL
SANS, MONO = "DejaVu Sans", "DejaVu Sans Mono"


def load_csv(path):
    with open(path, newline="") as f:
        return [{"time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


def _event_time(setup: dict, event: str) -> datetime | None:
    for e in reversed(setup.get("events", [])):
        if e.get("event") == event and (e.get("detected_at") or e.get("time")):
            return datetime.fromisoformat(e.get("detected_at") or e["time"])   # when the cycle saw it
    return None


def kz_window(setup: dict) -> tuple[datetime | None, datetime | None]:
    """(start, end) of the setup's kill zone in SAST from its record."""
    end = datetime.fromisoformat(setup["killzone_end"]).astimezone(SAST) if setup.get("killzone_end") else None
    m = re.search(r"(\d\d):(\d\d)-(\d\d):(\d\d) SAST", setup.get("killzone", ""))
    start = None
    if m and end:
        start = end.replace(hour=int(m.group(1)), minute=int(m.group(2)))
        if start > end:
            start -= timedelta(days=1)
    return start, end


def countdown(setup: dict, now: datetime, event: str) -> dict:
    """Kill-zone progress and the line under it. Pure function (tested)."""
    start, end = kz_window(setup)
    out = {"name": setup.get("killzone", "").split(" (")[0] or "Kill zone", "frac": None, "left": "", "note": ""}
    if not end:
        return out
    now = now.astimezone(SAST)
    if start:
        span = (end - start).total_seconds()
        out["frac"] = min(1.0, max(0.0, (now - start).total_seconds() / span)) if span > 0 else None
        out["window"] = f"{start:%H:%M}-{end:%H:%M} SAST / {start.astimezone(NY):%H:%M}-{end.astimezone(NY):%H:%M} NY"
    left = end - now
    if left.total_seconds() > 0:
        h, m = divmod(int(left.total_seconds() // 60), 60)
        out["left"] = f"{h}h {m:02d}m left" if h else f"{m}m left"
    else:
        out["left"] = "kill zone over"
    status_like = event.lower()
    if status_like in ("new", "pending", "update"):
        out["note"] = f"Unfilled at {end:%H:%M} SAST -> auto-cancel alert" if left.total_seconds() > 0 else "Ended unfilled"
    elif status_like == "filled":
        out["note"] = "Filled - tracking until TP2, stop or breakeven"
    elif status_like == "tp1":
        out["note"] = "TP1 banked - rest open toward TP2"
    else:
        out["note"] = "Closed"
    return out


def _fmt(x, d):
    return "n/a" if x is None else f"{x:,.{d}f}"


def render(setup: dict, bars: list[dict], out: str, event: str = "NEW", last: int = 120, now: datetime | None = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    if now is None:
        now = _event_time(setup, event) or (datetime.fromisoformat(bars[-1]["time"]) + timedelta(minutes=1) if bars else None)
    if now is not None:
        bars = [b for b in bars if datetime.fromisoformat(b["time"]) + timedelta(minutes=1) <= now]
    bars = bars[-last:]
    if not bars:
        raise ValueError("no bars to draw")
    d = setup.get("decimals", 2)
    short = setup["direction"] == "short"
    sw = setup.get("sweep") or {}
    tg = setup.get("targets") or []
    status = palette.colors(event)
    plt.rcParams.update({"font.family": SANS, "text.color": T["tx"], "axes.edgecolor": T["line"]})
    W, H = 7.2, 10.2
    fig = plt.figure(figsize=(W, H), dpi=150, facecolor=T["bg"])

    def box(x, y, w, h, fc, ec=None, r=0.012, lw=0.8):
        fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", transform=fig.transFigure,
                                          fc=fc, ec=ec or fc, lw=lw, zorder=0))

    def txt(x, y, s, size=10, color=T["tx"], weight="normal", ha="left", va="center", mono=False):
        fig.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha, va=va, family=MONO if mono else SANS)

    box(0.03, 0.015, 0.94, 0.97, T["panel"], T["line"], r=0.02)
    # ---- header
    txt(0.07, 0.950, setup["instrument"], 22, weight="bold")
    dc = palette.DIRECTION[setup["direction"]]
    dx = 0.07 + 0.033 * len(setup["instrument"])
    box(dx, 0.938, 0.15, 0.025, dc["bg"], r=0.006)
    txt(dx + 0.075, 0.9505, ("▼ SHORT" if short else "▲ LONG"), 10.5, dc["fg"], "bold", ha="center")
    pill = palette.label("new" if event == "NEW" else event)
    pw = 0.03 + 0.019 * len(pill)
    box(0.93 - pw, 0.937, pw, 0.028, status["bg"], status["border"], r=0.012)
    txt(0.93 - pw / 2, 0.951, pill, 10.5, status["fg"], "bold", ha="center")
    lab = setup.get("bias_label", "")
    model = (setup.get("model") or "").split(" (")[0].split(" after")[0]
    charter = (setup.get("charter_model") or "").split(" ")[0]
    txt(0.07, 0.914, " · ".join(x for x in (model, f"Charter {charter}" if charter else "", lab) if x)[:60], 9.5, T["tx2"])
    stamp = now.astimezone(SAST) if now else None
    txt(0.93, 0.914, f"{setup['id']}  ·  {stamp:%H:%M} SAST" if stamp else setup["id"], 9.5, T["tx2"], ha="right", mono=True)
    # ---- kill zone + countdown
    cd = countdown(setup, now, event) if now else {"name": setup.get("killzone", ""), "frac": None, "left": "", "note": ""}
    box(0.06, 0.835, 0.88, 0.060, T["bg2"], T["line"], r=0.01)
    txt(0.08, 0.878, cd["name"], 10.5, palette.STATUS["pending"]["fg"], "bold")
    txt(0.08 + 0.0165 * len(cd["name"]) + 0.01, 0.878, cd.get("window", ""), 8.5, T["mut"], mono=True)
    txt(0.92, 0.878, cd["left"], 10, T["tx"], "bold", ha="right", mono=True)
    if cd["frac"] is not None:
        box(0.08, 0.856, 0.84, 0.006, T["line2"], r=0.003)
        box(0.08, 0.856, max(0.006, 0.84 * cd["frac"]), 0.006, palette.STATUS["pending"]["fg"], r=0.003)
    txt(0.08, 0.844, cd["note"], 8, T["mut"])
    # ---- chart
    ax = fig.add_axes([0.07, 0.395, 0.66, 0.425], facecolor=T["bg2"])
    ax.set_zorder(5)
    for s in ax.spines.values():
        s.set_color(T["line"])
    n = len(bars)
    times = [b["time"] for b in bars]
    for i, b in enumerate(bars):
        col = T["up"] if b["close"] >= b["open"] else T["down"]
        ax.vlines(i, b["low"], b["high"], color=col, linewidth=0.7, zorder=3)
        lo, hi = sorted((b["open"], b["close"]))
        ax.add_patch(Rectangle((i - 0.32, lo), 0.64, max(hi - lo, 1e-9), color=col, zorder=3))
    lvls = [x for x in [setup.get("entry"), setup.get("stop"), sw.get("extreme"), sw.get("level"), *(t["price"] for t in tg)] if x is not None]
    lo = min([b["low"] for b in bars] + lvls)
    hi = max([b["high"] for b in bars] + lvls)
    pad = (hi - lo) * 0.06 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, n + 1)

    x0 = times.index(sw["extreme_time_iso"]) if sw.get("extreme_time_iso") in times else max(0, n // 2)
    legend = []

    def zone(z, color, text):
        if z and z.get("low") is not None and z.get("high") is not None:
            ax.add_patch(Rectangle((x0, z["low"]), n + 1 - x0, z["high"] - z["low"], color=color, alpha=0.16, zorder=1, lw=0))
            legend.append((text, color))

    zone(setup.get("fvg"), P["fvg"], "FVG")
    zone(setup.get("ote"), P["ote"], "OTE 62-79%")
    zone((setup.get("unicorn") or {}).get("overlap"), P["unicorn"], "Unicorn")
    if sw.get("level") is not None:
        ax.axhline(sw["level"], color=P["sweep"], ls=(0, (3, 3)), lw=0.8, alpha=0.7, zorder=2)
        legend.append(("swept level", P["sweep"]))
    mss = (setup.get("mss") or {}).get("level")
    if mss is not None:
        ax.axhline(mss, color=P["mss"], ls=(0, (1, 2)), lw=0.8, alpha=0.6, zorder=2)
        legend.append(("MSS", P["mss"]))
    lx = 0.015
    for text, col in legend:
        ax.text(lx, 0.975, "■ " + text, transform=ax.transAxes, color=col, fontsize=7, va="top", zorder=7)
        lx += 0.045 + 0.0145 * len(text)
    if sw.get("extreme_time_iso") in times:
        i = times.index(sw["extreme_time_iso"])
        y = bars[i]["high"] if short else bars[i]["low"]
        ax.plot(i, y, marker="o", color=P["sweep"], markersize=5, zorder=6)
        ax.annotate(f"sweep {_fmt(sw.get('extreme'), d)}", (i, y), textcoords="offset points", xytext=(0, 7 if short else -11),
                    color=P["sweep"], fontsize=7, ha="center", weight="bold")
    tags = [("SL", setup.get("stop"), P["stop"], "-"), ("ENTRY", setup.get("entry"), P["entry"], "-")]
    tags += [(t["name"], t["price"], P["target"], "--") for t in tg]
    for name, y, col, ls in tags:
        if y is None:
            continue
        ax.axhline(y, color=col, ls=ls, lw=1.1, zorder=4)
    # right-edge price tags (figure coords so they sit outside the plot)
    y0, y1 = ax.get_ylim()
    placed = []
    for name, y, col, _ in sorted([t for t in tags if t[1] is not None], key=lambda t: -t[1]):
        fy = 0.395 + 0.425 * (y - y0) / (y1 - y0)
        for p in placed:  # keep tags apart
            if abs(fy - p) < 0.024:
                fy = p - 0.024
        placed.append(fy)
        box(0.74, fy - 0.011, 0.19, 0.022, col, r=0.004)
        r = next((t.get("r") for t in tg if t["name"] == name), None)
        txt(0.75, fy, name if r is None else f"{name} {r}R", 7.5, T["bg"], "bold")
        txt(0.925, fy, _fmt(y, d), 8, T["bg"], "bold", ha="right", mono=True)
    step = max(1, n // 5)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{datetime.fromisoformat(times[i]).astimezone(SAST):%H:%M}\n"
                        f"{datetime.fromisoformat(times[i]).astimezone(NY):%H:%M} NY" for i in ticks],
                       fontsize=7, color=T["mut"], family=MONO)
    ax.tick_params(axis="y", length=0, labelleft=False)   # prices are on the right-edge tags
    ax.grid(color=T["line"], lw=0.5, alpha=0.6)
    # ---- levels row
    cells = [("ENTRY", setup.get("entry"), P["entry"]), ("STOP", setup.get("stop"), P["stop"])]
    cells += [(f"{t['name']} · {t.get('r')}R" + (" PARTIAL" if t.get("partial") else ""), t["price"], P["target"]) for t in tg[:2]]
    cw = 0.88 / max(1, len(cells))
    for k, (name, val, col) in enumerate(cells):
        x = 0.06 + k * cw
        box(x + 0.004, 0.268, cw - 0.008, 0.075, T["bg2"], T["line"], r=0.008)
        txt(x + 0.02, 0.325, name, 7.5, T["mut"], mono=True)
        txt(x + 0.02, 0.290, _fmt(val, d), 13, col, "bold", mono=True)
    # ---- chips
    chips = [f"Risk {setup.get('risk_pct', '?')}% {setup.get('risk_mode', '')}".strip(),
             f"Stop {setup.get('risk_pts', '?')} pts", f"Bias: {lab or 'n/a'}", f"{setup.get('timeframe', '1m')} · times SAST"]
    src = setup.get("data_source") or "UNKNOWN SOURCE"
    chips2 = [("Data " + ("yfinance FALLBACK " if setup.get("fallback") else "") + src)[:70]]
    x = 0.06
    for c in chips:
        w = 0.022 + 0.0118 * len(c)
        col = palette.STATUS["stop"]["fg"] if c.startswith("Bias: COUNTER") else T["tx2"]
        box(x, 0.205, w, 0.034, T["panel2"], T["line2"], r=0.008)
        txt(x + w / 2, 0.222, c, 8, col, ha="center")
        x += w + 0.012
    for c in chips2:
        w = min(0.88, 0.022 + 0.0118 * len(c))
        box(0.06, 0.158, w, 0.034, T["panel2"], T["line2"], r=0.008)
        txt(0.06 + w / 2, 0.175, c, 8, palette.STATUS["filled"]["fg"] if setup.get("fallback") else T["tx2"], ha="center")
    # ---- why + footer
    why = []
    if sw.get("description"):
        why.append(f"Swept: {sw['description']}")
    if (setup.get("mss") or {}).get("description"):
        why.append(f"MSS: {setup['mss']['description']}")
    for k, line in enumerate(why[:2]):
        txt(0.07, 0.122 - k * 0.026, line[:86], 7.8, T["tx2"])
    fig.add_artist(plt.Line2D([0.06, 0.94], [0.040, 0.040], transform=fig.transFigure, color=T["line"], lw=0.8))
    txt(0.07, 0.024, "Analysis only, not financial advice. Nothing here places or manages orders.", 7.5, T["mut"])
    if "EXAMPLE" in src.upper() or setup.get("example"):
        txt(0.93, 0.024, "EXAMPLE DATA", 7.5, palette.STATUS["filled"]["fg"], "bold", ha="right")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=T["bg"])
    plt.close(fig)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", required=True); p.add_argument("--bars", required=True); p.add_argument("--out", required=True)
    p.add_argument("--event", default="NEW"); p.add_argument("--last", type=int, default=120)
    p.add_argument("--now", help="ISO time the alert is for (default: the event's time in the setup, else last bar)")
    a = p.parse_args(argv)
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("error: matplotlib not installed (pip install matplotlib)", file=sys.stderr); return 2
    with open(a.setup) as f:
        s = json.load(f)
    print(render(s, load_csv(a.bars), a.out, a.event, a.last, datetime.fromisoformat(a.now) if a.now else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
