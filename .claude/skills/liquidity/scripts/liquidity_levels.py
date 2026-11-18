#!/usr/bin/env python3
"""Liquidity map from intraday bars: PDH/PDL, Asia range, session/week extremes, equal highs/lows.
Each level is tagged buy-side (above price) or sell-side (below) and swept/untouched since it formed.

    python3 .claude/skills/liquidity/scripts/liquidity_levels.py --csv drafts/bars/US100_5m.csv \
        --date 2026-11-12 [--daily drafts/bars/US100_1d.csv] [--eq-tol 3.0]
Bar times must be ISO 8601 with offset (SAST from fetch_bars.py). Asia = 20:00-00:00 New York, DST-correct.
"""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY, SAST = ZoneInfo("America/New_York"), ZoneInfo("Africa/Johannesburg")


def load_csv(path):
    with open(path, newline="") as f:
        return [{"time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=SAST)


def asia_window(trading_day: date) -> tuple[datetime, datetime]:
    s = datetime.combine(trading_day - timedelta(days=1), time(20), NY)
    e = datetime.combine(trading_day, time(0), NY)
    return s.astimezone(SAST), e.astimezone(SAST)


def _swept(bars, after: datetime, level: float, side: str) -> dict | None:
    for b in bars:
        if _dt(b["time"]) <= after:
            continue
        if (side == "buy" and b["high"] > level) or (side == "sell" and b["low"] < level):
            return {"time": b["time"], "price": b["high"] if side == "buy" else b["low"]}
    return None


def equal_extremes(bars, tol: float, n: int = 2) -> list[dict]:
    out = []
    for key, side in (("high", "buy"), ("low", "sell")):
        pts = []
        for i in range(n, len(bars) - n):
            v = bars[i][key]
            ok = all((v > bars[j][key]) if key == "high" else (v < bars[j][key]) for j in range(i - n, i)) and \
                 all((v >= bars[j][key]) if key == "high" else (v <= bars[j][key]) for j in range(i + 1, i + n + 1))
            if ok:
                pts.append((i, v))
        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                if abs(pts[a][1] - pts[b][1]) <= tol:
                    lvl = max(pts[a][1], pts[b][1]) if side == "buy" else min(pts[a][1], pts[b][1])
                    out.append({"name": f"equal {key}s", "level": lvl, "side": side,
                                "formed": bars[pts[b][0]]["time"]})
    return out


def liquidity_map(bars: list[dict], trading_day: date, daily: list[dict] | None = None, eq_tol: float = 0.0) -> dict:
    if not bars:
        raise ValueError("no bars")
    last = bars[-1]["close"]
    levels = []
    a0, a1 = asia_window(trading_day)
    asia = [b for b in bars if a0 <= _dt(b["time"]) < a1]
    if asia:
        levels += [{"name": "Asia high", "level": max(b["high"] for b in asia), "formed": a1.isoformat()},
                   {"name": "Asia low", "level": min(b["low"] for b in asia), "formed": a1.isoformat()}]
    if daily and len(daily) >= 2:
        prev = daily[-2]
        start = datetime.combine(trading_day - timedelta(days=1), time(17), NY).astimezone(SAST)
        levels += [{"name": "PDH", "level": prev["high"], "formed": start.isoformat()},
                   {"name": "PDL", "level": prev["low"], "formed": start.isoformat()}]
    monday = trading_day - timedelta(days=trading_day.weekday())
    wk_start = datetime.combine(monday - timedelta(days=1), time(17), NY).astimezone(SAST)
    day_start = datetime.combine(trading_day - timedelta(days=1), time(17), NY).astimezone(SAST)
    prior_week = [b for b in bars if wk_start <= _dt(b["time"]) < day_start]
    if prior_week:
        levels += [{"name": "week high (before today)", "level": max(b["high"] for b in prior_week), "formed": day_start.isoformat()},
                   {"name": "week low (before today)", "level": min(b["low"] for b in prior_week), "formed": day_start.isoformat()}]
    if eq_tol > 0:
        levels += equal_extremes(bars, eq_tol)
    out = []
    for lv in levels:
        side = lv.get("side") or ("buy" if "high" in lv["name"].lower() or lv["name"] == "PDH" else "sell")
        sw = _swept(bars, _dt(lv["formed"]), lv["level"], side)
        out.append({**lv, "side": side, "swept": sw, "distance": round(lv["level"] - last, 6)})
    buy = sorted([x for x in out if x["side"] == "buy"], key=lambda x: abs(x["distance"]))
    sell = sorted([x for x in out if x["side"] == "sell"], key=lambda x: abs(x["distance"]))
    return {"date": trading_day.isoformat(), "last": last, "last_time": bars[-1]["time"],
            "asia_window_sast": f"{a0:%H:%M}-{a1:%H:%M}", "buy_side": buy, "sell_side": sell}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True); p.add_argument("--date", required=True)
    p.add_argument("--daily"); p.add_argument("--eq-tol", type=float, default=0.0)
    a = p.parse_args(argv)
    try:
        res = liquidity_map(load_csv(a.csv), date.fromisoformat(a.date), load_csv(a.daily) if a.daily else None, a.eq_tol)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
