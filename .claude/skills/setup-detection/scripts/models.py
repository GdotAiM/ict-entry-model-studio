#!/usr/bin/env python3
"""Model detectors on top of detect.py: the Unicorn (breaker block + FVG overlap) and the ICT Charter Price Action
Models 1-13 (reference: .claude/skills/ict-entry-models/references/charter-models.md).

Honesty rules:
  * Every Charter check is either computed from bars/context (True/False) or None = "not computable here".
  * status: "detected" (all computable checks pass, nothing essential missing), "partial" (>= half pass),
    "not detected", or "not mechanical" (the model depends on data this project does not have, e.g. COT, seasonality).
  * Pip-sized targets in the Charter models are FX-calibrated. For US100 / US500 / XAUUSD those checks are None, not False.

    python3 .claude/skills/setup-detection/scripts/models.py --csv bars.csv --direction short --level 20030 [--context ctx.json]
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect  # noqa: E402

NY = ZoneInfo("America/New_York")
FX = {"EURUSD", "GBPUSD"}
INDICES = {"US100", "US500", "NAS100"}  # NAS100 = old OANDA name, kept so older setups still classify


# ---------------------------------------------------------------- Unicorn
def breaker_block(bars, mss: dict, direction: str, back: int = 5, fwd: int = 2) -> dict | None:
    """Bearish breaker (shorts): the highest up-close candle at the swing low that the MSS broke (ICT: 'highest up-close
    candle in the most recent swing low before the old high was taken'). Bullish breaker mirrors with down-close candles
    at the broken swing high. Range = that candle's low-high."""
    if not mss or not mss.get("broken"):
        return None
    i0 = mss["swing_index"]
    rng = range(max(0, i0 - back), min(len(bars), i0 + fwd + 1))
    if direction == "short":
        c = [k for k in rng if bars[k]["close"] > bars[k]["open"]]
        if not c:
            return None
        k = max(c, key=lambda x: bars[x]["high"])
    else:
        c = [k for k in rng if bars[k]["close"] < bars[k]["open"]]
        if not c:
            return None
        k = min(c, key=lambda x: bars[x]["low"])
    return {"index": k, "time": bars[k]["time"], "low": bars[k]["low"], "high": bars[k]["high"]}


def unicorn(bars, mss: dict, direction: str, ote: dict | None = None) -> dict | None:
    """Unicorn = a breaker block overlapping an FVG of the MSS leg. If an OTE is given, the overlap must touch it.
    Entry = the overlap price nearest the OTE sweet spot (or the overlap midpoint without an OTE)."""
    bb = breaker_block(bars, mss, direction)
    if not bb:
        return None
    for g in reversed(mss.get("fvgs", [])):
        lo, hi = max(bb["low"], g["low"]), min(bb["high"], g["high"])
        if lo > hi:
            continue
        if ote and (hi < ote["low"] or lo > ote["high"]):
            continue
        target = ote["sweet"] if ote else (lo + hi) / 2
        entry = min(max(target, lo), hi)
        return {"breaker": bb, "fvg": g, "overlap": {"low": lo, "high": hi}, "entry": entry}
    return None


# ---------------------------------------------------------------- Charter models
def _pips(instrument: str, dist: float | None, pip: float | None) -> float | None:
    if dist is None or pip is None or instrument not in FX:
        return None
    return abs(dist) / pip


def _rng(x, lo, hi):
    return None if x is None else lo <= x <= hi


def _ny(ctx) -> datetime | None:
    t = ctx.get("entry_time")
    if not t:
        return None
    d = datetime.fromisoformat(t)
    return d.astimezone(NY) if d.tzinfo else None


def _in_ny(ctx, start: str, end: str) -> bool | None:
    d = _ny(ctx)
    if d is None:
        return None
    hm = d.strftime("%H:%M")
    return start <= hm < end


def _weekday(ctx) -> int | None:
    d = _ny(ctx)
    return d.weekday() if d else None


def _status(checks: dict, essential: tuple = ()) -> str:
    """detected: every computable check passes, >= 60% of checks computable and every essential check computed & True.
    partial: >= half of the computable checks pass. insufficient data: too few checks computable to say."""
    vals = [v for v in checks.values() if v is not None]
    if not vals:
        return "not mechanical"
    enough = len(vals) >= max(2, -(-len(checks) * 3 // 5))
    if all(vals) and enough and all(checks.get(e) is True for e in essential):
        return "detected"
    if not enough:
        return "insufficient data"
    return "partial" if sum(vals) >= len(vals) / 2 else "not detected"


def charter_models(ctx: dict) -> list[dict]:
    """ctx keys (missing -> None): instrument, direction, entry_time (ISO with offset), entry_tf, sweep_label,
    sweep_tf, mss_valid, fvg_side_of_eq ('premium'|'discount'), in_ote, unicorn, zone_pct (20d dealing range %),
    daily_took_20d ('high'|'low'|None), weekly_bias, daily_bias, bias_confidence, tp1_dist, pip,
    london_direction ('up'|'down'), target_is_old_daily (bool), weekly_expansion (bool), liquidity_unswept (bool)."""
    inst, d = ctx.get("instrument"), ctx.get("direction")
    long = d == "long"
    wd = _weekday(ctx)
    tp_p = _pips(inst, ctx.get("tp1_dist"), ctx.get("pip"))
    zone = ctx.get("zone_pct")
    disc_ok = None if zone is None else (zone <= 50 if long else zone >= 50)
    wbias_ok = None if not ctx.get("weekly_bias") or ctx["weekly_bias"] == "neutral" else ctx["weekly_bias"] == ("bull" if long else "bear")
    ltf = None if not ctx.get("entry_tf") else ctx["entry_tf"] in ("1m", "3m", "5m")
    raid = ctx.get("sweep_label")
    pd_raid = None if raid is None else any(x in raid for x in ("PDH", "PDL"))
    lon_open = _in_ny(ctx, "02:00", "05:00")
    ny_open = _in_ny(ctx, "07:00", "10:00")
    open_kz = None if lon_open is None else bool(lon_open or ny_open)
    out = []

    def add(code, name, checks, essential=(), note=""):
        out.append({"code": code, "name": name, "status": _status(checks, essential),
                    "checks": checks, "note": note})

    # M1 intraday scalp 15-20 pips (NY 07-10/11, Mon-Wed, Thu only if liquidity unswept, no Fri)
    took = ctx.get("daily_took_20d")
    add("M1", "Intraday scalp 15-20 pips", {
        "daily took a 20-day swing (high for buys, low for sells)": None if took is None else took == ("high" if long else "low"),
        "price at EQ/discount (buy) or EQ/premium (sell)": disc_ok,
        "NY kill zone 07:00-11:00 NY": _in_ny(ctx, "07:00", "11:00"),
        "Mon-Wed, or Thu with liquidity unswept, never Fri": None if wd is None else (wd <= 2 or (wd == 3 and bool(ctx.get("liquidity_unswept")))),
        "against London's move": None if not ctx.get("london_direction") else ctx["london_direction"] == ("down" if long else "up"),
        "OTE entry": ctx.get("in_ote"),
        "target 15-20 pips (FX only)": _rng(tp_p, 12, 25)}, essential=("NY kill zone 07:00-11:00 NY",),
        note="5m OTE +/-5 pip offset and 'one and done' are execution rules, not detectable from bars")
    # M2 weekly range expansion
    add("M2", "Short-term weekly range expansion", {
        "weekly bias agrees": wbias_ok, "Tue-Thu": None if wd is None else 1 <= wd <= 3,
        "NY open": ny_open, "weekly expansion underway": ctx.get("weekly_expansion")})
    # M3 swing / M4 position: need COT + seasonality
    add("M3", "Swing", {"daily draw on liquidity agrees": wbias_ok, "COT commercials vs 12-month midpoint": None},
        note="NOT MECHANICAL HERE: needs COT data, which this project does not fetch")
    out[-1]["status"] = "not mechanical"
    add("M4", "Position (500+ pips)", {"20/40/60 IPDA + seasonality + COT": None},
        note="NOT MECHANICAL HERE: position-trade model (4H FVG, COT, seasonality) is outside an intraday watcher")
    # M5 day trade 40-50 pips
    add("M5", "Day-trade volatility expansion 40-50 pips", {
        "Tue-Thu": None if wd is None else 1 <= wd <= 3, "London/NY open": open_kz,
        "entry in discount (long) / premium (short) of 20d range": disc_ok,
        "15m FVG entry": None if not ctx.get("entry_tf") else ctx["entry_tf"] == "15m",
        "target 40-50 pips (FX only)": _rng(tp_p, 35, 60)})
    # M6 / M7 universal buy/sell-side
    draw_ok = None if not ctx.get("daily_bias") or ctx["daily_bias"] == "neutral" else ctx["daily_bias"] == ("bull" if long else "bear")
    run = ctx.get("mss_valid")
    if long:
        add("M6", "Universal buy-side (market maker buy model)", {
            "draw to a premium array above (daily bias bull)": draw_ok, "sell-stop run then MSS up": run,
            "entry FVG/void in discount": disc_ok}, note="pyramiding '<50% of the run done' is a management rule")
    else:
        add("M7", "Universal sell-side (market maker sell model)", {
            "draw to a discount array below (daily bias bear)": draw_ok, "buy-stop run then MSS down": run,
            "entry FVG/void in premium": disc_ok}, note="pyramiding '<50% of the run done' is a management rule")
    # M8 25 pips/week
    add("M8", "25 pips a week", {"weekly bias agrees": wbias_ok, "London/NY open": open_kz,
                                 "stop raid entry": bool(raid) if raid is not None else None, "target ~25 pips (FX only)": _rng(tp_p, 18, 35)})
    # M9 one shot one kill (judgement)
    conf = ctx.get("bias_confidence")
    add("M9", "One shot, one kill", {"bias aligned (daily)": draw_ok, "high-confidence bias (>= 0.66)": None if conf is None else conf >= 0.66,
                                     "raid + valid MSS": run, "PD array entry (FVG/OTE/Unicorn)": bool(ctx.get("in_ote") or ctx.get("unicorn"))},
        essential=("high-confidence bias (>= 0.66)", "bias aligned (daily)"),
        note="judgement model: the script only checks alignment; conviction is Ntloso's call")
    # M10 swing 50-75 pips/week
    add("M10", "Swing 50-75 pips a week", {"weekly bias agrees": wbias_ok, "Mon-Wed anchor": None if wd is None else wd <= 2,
                                           "15m liquidity pool raid": None if not ctx.get("sweep_tf") else ctx["sweep_tf"] in ("15m", "1h"),
                                           "London/NY open": open_kz, "target 50-75 pips (FX only)": _rng(tp_p, 45, 85)})
    # M11 day trade ~30 pips
    add("M11", "Day trade ~30 pips", {"weekly bias agrees": wbias_ok, "old daily high/low as the draw": ctx.get("target_is_old_daily"),
                                      "retrace into internal range liquidity (FVG/OTE)": ctx.get("in_ote"),
                                      "target ~30 pips (FX only)": _rng(tp_p, 22, 40)})
    # M12 scalp ~20 pips
    add("M12", "Scalp ~20 pips", {"PDH/PDL or intraday liquidity raid": None if raid is None else True,
                                  "PDH/PDL raid": pd_raid, "15m or lower": ltf if ltf is not None else None,
                                  "target ~20 pips (FX only)": _rng(tp_p, 12, 30)}, essential=("PDH/PDL or intraday liquidity raid",))
    # M13 2022 model
    win = _in_ny(ctx, "08:30", "11:00") if inst in INDICES else _in_ny(ctx, "07:00", "10:00")
    add("M13", "2022 model", {
        "liquidity raid": None if raid is None else True, "fast MSS with displacement + FVG": run, "5m-1m entry timeframe": ltf,
        "canonical window (FX 07-10 NY, indices 08:30-11 NY)": win if not lon_open else None,
        "FVG on the right side of equilibrium": None if not ctx.get("fvg_side_of_eq") else ctx["fvg_side_of_eq"] == ("discount" if long else "premium")},
        essential=("liquidity raid", "fast MSS with displacement + FVG", "5m-1m entry timeframe"),
        note="London Open use of the 2022 model is Ntloso's practice; the canonical window check is skipped there")
    return out


PRIORITY = ["M13", "M12", "M1", "M11", "M8", "M10", "M5", "M2", "M6", "M7", "M9", "M3", "M4"]


def name_models(unicorn_hit: dict | None, charters: list[dict]) -> dict:
    """Primary model name for the alert + best Charter model + the others that fit."""
    det = [c for c in charters if c["status"] == "detected"]
    part = [c for c in charters if c["status"] == "partial"]
    rank = lambda c: PRIORITY.index(c["code"])
    best = sorted(det, key=rank)[:1] or sorted(part, key=rank)[:1]
    m13 = next((c for c in charters if c["code"] == "M13"), None)
    if unicorn_hit:
        model = "Unicorn (breaker + FVG overlap) after a liquidity raid"
    elif m13 and m13["status"] == "detected":
        model = "ICT 2022 model: raid + MSS + FVG/OTE"
    else:
        model = "Core: raid + MSS with displacement + FVG/OTE"
    cm = f"{best[0]['code']} {best[0]['name']} ({best[0]['status']})" if best else "none matched"
    also = [f"{c['code']} ({c['status']})" for c in sorted(det + part, key=rank) if not best or c["code"] != best[0]["code"]]
    return {"model": model, "charter_model": cm + (f"; also fits {', '.join(also[:3])}" if also else ""),
            "charter_best": best[0]["code"] if best else None}


STAGES = {"watching": "Opportunity Discovery", "pending": "Trade Planning", "filled": "Trade Execution",
          "tp1": "Trade Management", "prep": "Preparation", "locked": "Trade Planning (locked: risk cap)"}


def charter_stage(status: str) -> str:
    return STAGES.get(status, "Trade Management (closed)" if status.startswith("closed") else "Trade Planning (cancelled)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True); p.add_argument("--direction", choices=["long", "short"], required=True)
    p.add_argument("--level", type=float, required=True); p.add_argument("--context", help="JSON file with charter context")
    a = p.parse_args(argv)
    bars = detect.load_csv(a.csv)
    c = detect.find_setup(bars, a.direction, [a.level])[0]
    if c["stage"] != "complete":
        print(json.dumps({"stage": c["stage"]})); return 1
    u = unicorn(bars, c["mss"], a.direction, c["ote"])
    ctx = json.load(open(a.context)) if a.context else {}
    ctx.setdefault("direction", a.direction); ctx.setdefault("mss_valid", True); ctx.setdefault("in_ote", True)
    ctx.setdefault("unicorn", bool(u)); ctx.setdefault("entry_time", bars[-1]["time"])
    ch = charter_models(ctx)
    print(json.dumps({"unicorn": u, "names": name_models(u, ch), "charter": ch}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
