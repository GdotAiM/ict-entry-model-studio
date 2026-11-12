#!/usr/bin/env python3
"""Marked-up candlestick chart for an alert: sweep, MSS level, FVG, OTE/entry zone, entry, stop, targets.
Uses matplotlib only (Agg backend, no display). Each alert gets its own PNG, sent before the text.

    python3 .claude/skills/chart-markup/scripts/render_chart.py --setup state/setups/N6.json \
        --bars drafts/bars/NAS100_1m.csv --out charts/nas_N6_1111_new.png [--event NEW] [--last 120]
"""
from __future__ import annotations
import argparse, csv, json, os, sys


def load_csv(path):
    with open(path, newline="") as f:
        return [{"time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


def render(setup: dict, bars: list[dict], out: str, event: str = "NEW", last: int = 120) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    bars = bars[-last:]
    if not bars:
        raise ValueError("no bars to draw")
    fig, ax = plt.subplots(figsize=(13, 7))
    for i, b in enumerate(bars):
        up = b["close"] >= b["open"]
        col = "#26a69a" if up else "#ef5350"
        ax.vlines(i, b["low"], b["high"], color=col, linewidth=0.8)
        lo, hi = sorted((b["open"], b["close"]))
        ax.add_patch(Rectangle((i - 0.35, lo), 0.7, max(hi - lo, 1e-9), color=col))
    n = len(bars)
    d = setup.get("decimals", 2)

    def hline(y, color, text, style="--"):
        if y is None:
            return
        ax.axhline(y, color=color, linestyle=style, linewidth=1)
        ax.text(n + 0.5, y, f" {text} {y:.{d}f}", color=color, va="center", fontsize=8,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1))

    def zone(z, color, text):
        if z and z.get("low") is not None and z.get("high") is not None:
            ax.axhspan(z["low"], z["high"], color=color, alpha=0.15)
            ax.text(1, z["high"], text, color=color, fontsize=8, va="bottom")

    zone(setup.get("fvg"), "purple", "FVG")
    zone(setup.get("ote"), "green", "OTE 62-79%")
    zone((setup.get("unicorn") or {}).get("overlap"), "goldenrod", "Unicorn (breaker+FVG)")
    sw = setup.get("sweep", {})
    hline(sw.get("level"), "orange", "swept liquidity", ":")
    hline(sw.get("extreme"), "darkorange", "raid extreme", ":")
    hline((setup.get("mss") or {}).get("level"), "purple", "MSS", "-.")
    hline(setup.get("entry"), "green", "ENTRY", "-")
    hline(setup.get("stop"), "red", "STOP", "-")
    for t in setup.get("targets", []):
        hline(t["price"], "blue", f"{t['name']} {t.get('r', '')}R")
    times = [b["time"] for b in bars]
    for key, mark, col in (("extreme_time_iso", "v" if setup["direction"] == "short" else "^", "darkorange"),):
        tt = sw.get(key)
        if tt in times:
            i = times.index(tt)
            y = bars[i]["high"] if setup["direction"] == "short" else bars[i]["low"]
            ax.plot(i, y, marker=mark, color=col, markersize=10)
            ax.annotate("sweep", (i, y), textcoords="offset points", xytext=(0, 10 if setup["direction"] == "short" else -15), color=col, fontsize=8, ha="center")
    step = max(1, n // 10)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([t[11:16] for t in times[::step]], fontsize=8)
    ax.set_xlim(-1, n + 12)
    lab = setup.get("bias_label", "")
    src = "yfinance FALLBACK" if setup.get("fallback") else (setup.get("data_source") or "")
    ax.set_title(f"{setup['instrument']} {setup.get('timeframe', '1m')} {setup['direction'].upper()} {setup['id']} {event} | "
                 f"{lab} | {setup.get('killzone', '')} | times SAST\n{src} | Analysis only, not financial advice", fontsize=9)
    ax.grid(alpha=0.2)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fig.tight_layout(); fig.savefig(out, dpi=110); plt.close(fig)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", required=True); p.add_argument("--bars", required=True); p.add_argument("--out", required=True)
    p.add_argument("--event", default="NEW"); p.add_argument("--last", type=int, default=120)
    a = p.parse_args(argv)
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("error: matplotlib not installed (pip install matplotlib)", file=sys.stderr); return 2
    with open(a.setup) as f:
        s = json.load(f)
    print(render(s, load_csv(a.bars), a.out, a.event, a.last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
