#!/usr/bin/env python3
"""Multi-timeframe bias: monthly / weekly / daily structure + dealing-range premium/discount + draw on liquidity.
Outputs bias, confidence (0-1 and low/medium/high), invalidation level and the reasoning lines behind it.

Method (modelled on the team Bias Bot's daily file: M/W/D read, 20-day dealing range zone, ranked draws,
"wrong on a close beyond X", DXY cross-check):
  1. Build weekly (ISO week) and monthly bars from daily bars labelled by trading date.
  2. Per timeframe, two votes: structure (last two swing highs/lows: HH+HL bull, LH+LL bear) and the last CLOSED bar's
     close vs the bar before it (close above its high bull, below its low bear). tf score = sign(sum), each tf in {-1,0,1}.
     The forming bar's position vs the prior bar's range (took prior high / low) is reported as context.
  3. Weighted score = 1 x monthly + 1.5 x weekly + 2 x daily (max 4.5). |score| >= 1 -> bull/bear, >= 2.5 strong, else "mild".
  4. Dealing range (IPDA 20 days): position %. A bull bias in deep premium (>80% or above the range) or a bear bias in deep
     discount (<20% or below) is EXTENDED: confidence -0.15 and a reversal-risk note. Bull from discount / bear from premium +0.05.
  5. Draw on liquidity: nearest untaken pools above and below (PDH/PDL, PWH/PWL, PMH/PML, old daily swing highs/lows in 60 days).
     The draw is the pool on the bias side; with no bias, the nearer one.
  6. Optional DXY cross-check for EUR/GBP/XAU (inverse): agreement +0.1, disagreement -0.1 and a note.
  Confidence = clamp(|score| / 4.5 + adjustments, 0, 1); high >= 0.66, medium >= 0.4, else low.
  Invalidation: bear -> most recent daily swing high (else PDH); bull -> most recent daily swing low (else PDL).

    python3 .claude/skills/ict-bias/scripts/mtf_bias.py --csv drafts/bars/EURUSD_1d.csv [--dxy drafts/bars/DXY_1d.csv] [--last 1.10781]
Daily CSV time column = trading date YYYY-MM-DD (fetch_bars.py writes that for 1d). Needs >= 60 daily bars (>= 250 for a full monthly read).
"""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import date

W = {"monthly": 1.0, "weekly": 1.5, "daily": 2.0}
MAXW = sum(W.values())


def load_csv(path):
    with open(path, newline="") as f:
        return [{"time": r["time"][:10], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


def aggregate(daily: list[dict], period: str) -> list[dict]:
    """period 'W' (ISO week) or 'M' (calendar month) bars from trading-date daily bars."""
    out, keyf = [], (lambda d: d.isocalendar()[:2]) if period == "W" else (lambda d: (d.year, d.month))
    for b in daily:
        k = keyf(date.fromisoformat(b["time"]))
        if out and out[-1]["_k"] == k:
            o = out[-1]; o["high"] = max(o["high"], b["high"]); o["low"] = min(o["low"], b["low"]); o["close"] = b["close"]
        else:
            out.append({"_k": k, "time": b["time"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]})
    return [{k: v for k, v in o.items() if k != "_k"} for o in out]


def swings(bars, n):
    hs, ls = [], []
    for i in range(n, len(bars) - n):
        h, l = bars[i]["high"], bars[i]["low"]
        if all(h > bars[j]["high"] for j in range(i - n, i)) and all(h >= bars[j]["high"] for j in range(i + 1, i + n + 1)):
            hs.append((i, h))
        if all(l < bars[j]["low"] for j in range(i - n, i)) and all(l <= bars[j]["low"] for j in range(i + 1, i + n + 1)):
            ls.append((i, l))
    return hs, ls


def tf_read(bars: list[dict], name: str, n: int) -> dict:
    """bars include the forming bar last. Votes use closed bars only."""
    closed = bars[:-1]
    if len(closed) < 3:
        return {"tf": name, "score": 0, "structure": "insufficient data", "close": "insufficient data", "context": ""}
    hs, ls = swings(closed, n)
    st = 0
    if len(hs) >= 2 and len(ls) >= 2:
        st = 1 if hs[-1][1] > hs[-2][1] and ls[-1][1] > ls[-2][1] else -1 if hs[-1][1] < hs[-2][1] and ls[-1][1] < ls[-2][1] else 0
    last, prev = closed[-1], closed[-2]
    cv = 1 if last["close"] > prev["high"] else -1 if last["close"] < prev["low"] else 0
    s = st + cv
    score = (s > 0) - (s < 0)
    cur, ref = bars[-1], closed[-1]
    took = [x for x, ok in (("took prior high", cur["high"] > ref["high"]), ("took prior low", cur["low"] < ref["low"])) if ok]
    return {"tf": name, "score": score,
            "structure": {1: "bull (HH+HL)", -1: "bear (LH+LL)", 0: "mixed"}[st],
            "close": {1: "last close above prior high", -1: "last close below prior low", 0: "last close inside prior range"}[cv],
            "context": f"forming bar {' and '.join(took) or 'inside prior range'}"}


def draws(daily: list[dict], weekly: list[dict], monthly: list[dict], last: float, lookback: int = 60) -> dict:
    """Untaken pools only: a pool is taken once any later daily bar trades through it."""
    pools = []  # (name, level, side, index of the first daily bar AFTER the pool formed)
    if len(daily) >= 2:
        pools += [("PDH", daily[-2]["high"], "h", len(daily) - 1), ("PDL", daily[-2]["low"], "l", len(daily) - 1)]
    for agg, hn, ln in ((weekly, "PWH", "PWL"), (monthly, "PMH", "PML")):
        if len(agg) >= 2:
            start = next(i for i, b in enumerate(daily) if b["time"] >= agg[-1]["time"])
            pools += [(hn, agg[-2]["high"], "h", start), (ln, agg[-2]["low"], "l", start)]
    off = max(0, len(daily) - lookback)
    hs, ls = swings(daily[off:-1], 2)
    pools += [(f"old high {daily[off + i]['time']}", h, "h", off + i + 1) for i, h in hs]
    pools += [(f"old low {daily[off + i]['time']}", l, "l", off + i + 1) for i, l in ls]
    above, below, taken = [], [], []
    for name, lvl, side, k in pools:
        later = daily[k:]
        hit = any(b["high"] > lvl for b in later) if side == "h" else any(b["low"] < lvl for b in later)
        if hit:
            taken.append(name); continue
        (above if lvl > last else below).append({"name": name, "level": lvl})
    above.sort(key=lambda x: x["level"]); below.sort(key=lambda x: -x["level"])
    return {"above": above[:5], "below": below[:5], "taken": taken}


def mtf_bias(daily: list[dict], last: float | None = None, dxy: list[dict] | None = None, inverse_to_dxy: bool = False,
             range_days: int = 20) -> dict:
    if len(daily) < 60:
        raise ValueError("need at least 60 daily bars (250+ for a full monthly read)")
    last = daily[-1]["close"] if last is None else last
    weekly, monthly = aggregate(daily, "W"), aggregate(daily, "M")
    reads = [tf_read(monthly, "monthly", 1), tf_read(weekly, "weekly", 1), tf_read(daily, "daily", 2)]
    score = sum(W[r["tf"]] * r["score"] for r in reads)
    bias = "bull" if score >= 1 else "bear" if score <= -1 else "neutral"
    strength = "strong" if abs(score) >= 2.5 else "mild" if bias != "neutral" else ""
    conf = abs(score) / MAXW
    why = [f"{r['tf']}: {r['structure']}; {r['close']}; {r['context']} -> {('bull', 'neutral', 'bear')[1 - r['score']]}" for r in reads]
    why.append(f"weighted score {score:+g} of +/-{MAXW:g} (M x1, W x1.5, D x2)")
    rng = daily[-range_days - 1:-1]  # last N completed days
    hi, lo = max(b["high"] for b in rng), min(b["low"] for b in rng)
    pos = round(100 * (last - lo) / (hi - lo), 1) if hi > lo else 50.0
    zone = ("above the range (deep premium)" if pos > 100 else "below the range (deep discount)" if pos < 0 else
            "premium" if pos > 50 else "discount" if pos < 50 else "equilibrium")
    extended = (bias == "bull" and pos > 80) or (bias == "bear" and pos < 20)
    if extended:
        conf -= 0.15; why.append(f"EXTENDED: {bias} bias at {pos}% of the {range_days}d range - reversal risk, don't chase")
    elif (bias == "bull" and pos < 50) or (bias == "bear" and pos > 50):
        conf += 0.05; why.append(f"{bias} bias from {'discount' if bias == 'bull' else 'premium'} ({pos}%) - good location")
    else:
        why.append(f"dealing range position {pos}% ({zone})")
    if dxy and inverse_to_dxy and len(dxy) >= 60:
        d = mtf_bias(dxy)
        mirror = {"bull": "bear", "bear": "bull", "neutral": "neutral"}[d["bias"]]
        if bias != "neutral" and mirror == bias:
            conf += 0.1; why.append(f"DXY {d['bias']} confirms (inverse)")
        elif bias != "neutral" and d["bias"] != "neutral":
            conf -= 0.1; why.append(f"DXY {d['bias']} DISAGREES (inverse would be {mirror}) - flag it")
    dr = draws(daily, weekly, monthly, last)
    draw = None
    if bias == "bull" and dr["above"]:
        draw = dr["above"][0]
    elif bias == "bear" and dr["below"]:
        draw = dr["below"][0]
    elif bias != "neutral":
        why.append(f"no untaken pool {'above' if bias == 'bull' else 'below'} in the look-back: price is at new extremes "
                   "(open air) - draw is external range liquidity, not a mapped level")
    elif dr["above"] or dr["below"]:
        cands = dr["above"][:1] + dr["below"][:1]
        draw = min(cands, key=lambda x: abs(x["level"] - last))
    if draw:
        why.append(f"draw on liquidity: {draw['name']} {draw['level']}")
    hs, ls = swings(daily[:-1], 2)
    if bias == "bear":
        inv = {"level": hs[-1][1] if hs else daily[-2]["high"], "rule": "bias wrong on a 1H close above"}
    elif bias == "bull":
        inv = {"level": ls[-1][1] if ls else daily[-2]["low"], "rule": "bias wrong on a 1H close below"}
    else:
        inv = None
    conf = round(max(0.0, min(1.0, conf)), 2)
    return {"date": daily[-1]["time"], "last": last, "bias": bias, "strength": strength,
            "label": f"{strength} {bias}".strip(), "score": score, "confidence": conf,
            "confidence_label": "high" if conf >= 0.66 else "medium" if conf >= 0.4 else "low",
            "timeframes": reads, "dealing_range": {"days": range_days, "high": hi, "low": lo, "eq": (hi + lo) / 2,
                                                    "position_pct": pos, "zone": zone, "extended": extended},
            "draw": draw, "draws": dr, "invalidation": inv, "reasoning": why}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True); p.add_argument("--dxy"); p.add_argument("--last", type=float)
    p.add_argument("--inverse-to-dxy", action="store_true", help="instrument is quoted against USD (EUR, GBP, XAU)")
    p.add_argument("--range-days", type=int, default=20)
    a = p.parse_args(argv)
    try:
        res = mtf_bias(load_csv(a.csv), a.last, load_csv(a.dxy) if a.dxy else None, a.inverse_to_dxy or bool(a.dxy), a.range_days)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
