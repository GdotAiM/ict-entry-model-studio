#!/usr/bin/env python3
"""Deterministic ICT entry-model helpers on OHLC bars (pure stdlib).

Sequence checked: liquidity sweep / turtle soup -> market structure shift (MSS) with displacement
-> fair value gap (FVG) in the displacement leg -> entry at the FVG or in the OTE zone (62-79%).

    python3 .claude/skills/setup-detection/scripts/detect.py --csv drafts/bars/NAS100_1m.csv \
        --direction short --level 28167.1 [--level 28154.4 ...] [--since "2026-11-12T09:00"]

Output: JSON list of candidates (one per swept level that completed the sequence) or partial
progress ("stage": "swept" | "mss") so the scanner can report what is still missing.
These helpers find *candidates*. The setup-validator decides tradability (stop, R:R, labels).
Exit codes: 0 at least one complete candidate, 1 none complete, 2 usage error.
"""
from __future__ import annotations
import argparse, csv, json, statistics, sys

OTE_LOW, OTE_SWEET, OTE_HIGH = 0.62, 0.705, 0.79


# ---------- bars ----------
def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return [{"time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


# ---------- structure ----------
def swing_highs(bars, left: int = 2, right: int = 2) -> list[tuple[int, float]]:
    out = []
    for i in range(left, len(bars) - right):
        h = bars[i]["high"]
        if all(h > bars[j]["high"] for j in range(i - left, i)) and all(h >= bars[j]["high"] for j in range(i + 1, i + right + 1)):
            out.append((i, h))
    return out


def swing_lows(bars, left: int = 2, right: int = 2) -> list[tuple[int, float]]:
    out = []
    for i in range(left, len(bars) - right):
        lo = bars[i]["low"]
        if all(lo < bars[j]["low"] for j in range(i - left, i)) and all(lo <= bars[j]["low"] for j in range(i + 1, i + right + 1)):
            out.append((i, lo))
    return out


def equal_levels(points: list[tuple[int, float]], tolerance: float) -> list[dict]:
    """Pairs of swing points within `tolerance` of each other (equal highs / equal lows = resting liquidity)."""
    res = []
    for a in range(len(points)):
        for b in range(a + 1, len(points)):
            if abs(points[a][1] - points[b][1]) <= tolerance:
                pa, pb = points[a][1], points[b][1]
                res.append({"i": points[a][0], "j": points[b][0], "max": max(pa, pb), "min": min(pa, pb)})
    return res


# ---------- sweep ----------
def detect_sweep(bars, level: float, side: str, start: int = 0, reclaim_bars: int = 3) -> dict | None:
    """side='buy' (raid above a high -> short idea) or 'sell' (raid below a low -> long idea).
    Returns the first trade through `level` at/after `start`, the raid extreme (until the reclaim window ends),
    and whether price closed back inside within `reclaim_bars` bars (turtle soup)."""
    for i in range(start, len(bars)):
        b = bars[i]
        if (side == "buy" and b["high"] > level) or (side == "sell" and b["low"] < level):
            j_end = min(len(bars), i + reclaim_bars)
            window = bars[i:j_end]
            if side == "buy":
                ext_i = max(range(i, j_end), key=lambda k: bars[k]["high"])
                reclaimed = any(x["close"] < level for x in window)
                extreme = bars[ext_i]["high"]
            else:
                ext_i = min(range(i, j_end), key=lambda k: bars[k]["low"])
                reclaimed = any(x["close"] > level for x in window)
                extreme = bars[ext_i]["low"]
            return {"index": i, "time": b["time"], "level": level, "side": side,
                    "extreme": extreme, "extreme_index": ext_i, "extreme_time": bars[ext_i]["time"],
                    "turtle_soup": reclaimed}
    return None


def raid_extreme(bars, sweep: dict, until: int) -> tuple[int, float]:
    """Most extreme price from the sweep bar up to (excluding) `until` - the real raid high/low."""
    rng = range(sweep["index"], max(sweep["index"] + 1, until))
    if sweep["side"] == "buy":
        k = max(rng, key=lambda x: bars[x]["high"]); return k, bars[k]["high"]
    k = min(rng, key=lambda x: bars[x]["low"]); return k, bars[k]["low"]


# ---------- displacement / FVG ----------
def is_displacement(bars, i: int, lookback: int = 20, factor: float = 1.5) -> bool:
    """Body of bar i at least `factor` x median range of the prior `lookback` bars."""
    prior = bars[max(0, i - lookback):i]
    if len(prior) < 3:
        return False
    med = statistics.median(b["high"] - b["low"] for b in prior)
    body = abs(bars[i]["close"] - bars[i]["open"])
    return med > 0 and body >= factor * med


def find_fvgs(bars, direction: str, start: int = 0, end: int | None = None) -> list[dict]:
    """3-candle FVGs whose middle candle is in [start, end). direction='bearish'|'bullish'.
    Bearish: low[i-1] > high[i+1] -> gap (high[i+1], low[i-1]). Bullish: high[i-1] < low[i+1]."""
    end = len(bars) - 1 if end is None else min(end, len(bars) - 1)
    out = []
    for i in range(max(1, start), end):
        a, c = bars[i - 1], bars[i + 1]
        if direction == "bearish" and a["low"] > c["high"]:
            out.append({"index": i, "time": bars[i]["time"], "low": c["high"], "high": a["low"]})
        elif direction == "bullish" and a["high"] < c["low"]:
            out.append({"index": i, "time": bars[i]["time"], "low": a["high"], "high": c["low"]})
    return out


# ---------- MSS ----------
def detect_mss(bars, sweep: dict, left: int = 2, right: int = 2, lookback: int = 20, factor: float = 1.5,
               max_bars: int = 15) -> dict | None:
    """After a buy-side sweep, MSS = a candle CLOSE below the most recent swing low formed before the raid extreme
    (mirror for sell-side). Requires displacement (>=1 displacement candle in the leg) AND an FVG in the leg, and the
    break must come within `max_bars` bars of the raid extreme (a shift that takes longer is a grind, not an MSS)."""
    ext_i = sweep["extreme_index"]
    if sweep["side"] == "buy":
        lows = [p for p in swing_lows(bars, left, right) if p[0] < ext_i]
        if not lows:
            return None
        sw_i, sw = lows[-1]
        brk = next((k for k in range(ext_i + 1, len(bars)) if bars[k]["close"] < sw), None)
        fdir, ddir = "bearish", -1
    else:
        highs = [p for p in swing_highs(bars, left, right) if p[0] < ext_i]
        if not highs:
            return None
        sw_i, sw = highs[-1]
        brk = next((k for k in range(ext_i + 1, len(bars)) if bars[k]["close"] > sw), None)
        fdir, ddir = "bullish", 1
    if brk is None:
        return {"broken": False, "swing_level": sw, "swing_index": sw_i, "swing_time": bars[sw_i]["time"]}
    # the raid extreme can extend after the sweep bar; it is the extreme before the break
    k_ext, ext = raid_extreme(bars, sweep, brk)
    if brk - k_ext > max_bars:
        return {"broken": True, "valid": False, "late": True, "swing_level": sw, "swing_index": sw_i, "swing_time": bars[sw_i]["time"],
                "break_index": brk, "break_time": bars[brk]["time"], "raid_extreme": ext, "raid_extreme_index": k_ext,
                "raid_extreme_time": bars[k_ext]["time"], "leg_end": None, "displacement": False, "displacement_bars": [], "fvgs": [],
                "note": f"break came {brk - k_ext} bars after the raid extreme (> {max_bars}): slow shift, not an MSS"}
    leg = range(k_ext + 1, brk + 1)
    disp = [k for k in leg if is_displacement(bars, k, lookback, factor) and (bars[k]["close"] - bars[k]["open"]) * ddir > 0]
    fvgs = find_fvgs(bars, fdir, k_ext + 1, brk + 2)
    # leg extreme = furthest point of the displacement leg (up to 3 bars after the break)
    tail = range(k_ext + 1, min(len(bars), brk + 4))
    leg_end = min(bars[k]["low"] for k in tail) if ddir < 0 else max(bars[k]["high"] for k in tail)
    return {"broken": True, "swing_level": sw, "swing_index": sw_i, "swing_time": bars[sw_i]["time"],
            "break_index": brk, "break_time": bars[brk]["time"], "raid_extreme": ext, "raid_extreme_index": k_ext,
            "raid_extreme_time": bars[k_ext]["time"], "leg_end": leg_end,
            "displacement": bool(disp), "displacement_bars": [bars[k]["time"] for k in disp],
            "fvgs": fvgs, "valid": bool(disp) and bool(fvgs)}


# ---------- OTE / entry ----------
def ote_zone(direction: str, leg_high: float, leg_low: float) -> dict:
    """Retracement zone of the displacement leg. Short: price retraces UP 62-79% of (high-low) from the low."""
    rng = leg_high - leg_low
    if rng <= 0:
        raise ValueError("leg_high must be above leg_low")
    if direction == "short":
        return {"low": leg_low + OTE_LOW * rng, "sweet": leg_low + OTE_SWEET * rng, "high": leg_low + OTE_HIGH * rng}
    return {"high": leg_high - OTE_LOW * rng, "sweet": leg_high - OTE_SWEET * rng, "low": leg_high - OTE_HIGH * rng}


def suggest_entry(direction: str, ote: dict, fvgs: list[dict]) -> dict:
    """Prefer the FVG edge that sits inside the OTE (short: FVG high; long: FVG low); else the OTE 70.5% level."""
    for g in reversed(fvgs):
        edge = g["high"] if direction == "short" else g["low"]
        if ote["low"] <= edge <= ote["high"]:
            return {"price": edge, "basis": "FVG edge inside OTE", "fvg": g}
    for g in reversed(fvgs):
        if g["low"] <= ote["high"] and g["high"] >= ote["low"]:
            return {"price": ote["sweet"], "basis": "OTE 70.5% (FVG overlaps OTE)", "fvg": g}
    return {"price": ote["sweet"], "basis": "OTE 70.5% (no FVG overlap - weaker)", "fvg": fvgs[-1] if fvgs else None}


def find_setup(bars, direction: str, levels: list[float], since: int = 0, **kw) -> list[dict]:
    """Run the full sequence for each liquidity level. direction 'short' raids buy-side; 'long' raids sell-side."""
    side = "buy" if direction == "short" else "sell"
    res = []
    for lvl in levels:
        sw = detect_sweep(bars, lvl, side, since)
        if not sw:
            res.append({"level": lvl, "stage": "not swept"}); continue
        mss = detect_mss(bars, sw, **kw)
        if not mss or not mss.get("broken"):
            res.append({"level": lvl, "stage": "swept", "sweep": sw, "mss": mss}); continue
        if not mss["valid"]:
            res.append({"level": lvl, "stage": "mss (no displacement/FVG - NOT valid)", "sweep": sw, "mss": mss}); continue
        hi, lo = (mss["raid_extreme"], mss["leg_end"]) if direction == "short" else (mss["leg_end"], mss["raid_extreme"])
        ote = ote_zone(direction, hi, lo)
        entry = suggest_entry(direction, ote, mss["fvgs"])
        res.append({"level": lvl, "stage": "complete", "direction": direction, "sweep": sw, "mss": mss,
                    "ote": ote, "entry": entry, "raid_extreme": mss["raid_extreme"]})
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True); p.add_argument("--direction", choices=["long", "short"], required=True)
    p.add_argument("--level", type=float, action="append", required=True)
    p.add_argument("--since", help="ISO time prefix; only sweeps at/after this bar count")
    p.add_argument("--factor", type=float, default=1.5, help="displacement body / median range")
    a = p.parse_args(argv)
    bars = load_csv(a.csv)
    since = 0
    if a.since:
        since = next((i for i, b in enumerate(bars) if b["time"] >= a.since), len(bars))
    out = find_setup(bars, a.direction, a.level, since, factor=a.factor)
    print(json.dumps(out, indent=2))
    return 0 if any(c["stage"] == "complete" for c in out) else 1


if __name__ == "__main__":
    sys.exit(main())
