#!/usr/bin/env python3
"""Risk % and position size. Never sizes a live order; it prints a formula and numbers for you to check.

Risk rules:
  * Standalone trade: 0.5% of equity.
  * Correlated trades (same group): 0.25% each for the 2nd and later, max 4 open in the group, 1% group maximum,
    counted on the risk each open trade really carries (a 0.5% standalone + 0.25% + 0.25% = 1.0% = group full).
    Groups (one 1% cap each):
      US-indices-long / US-indices-short: US100 and US500 in the same direction (they move together, ~0.9+ correlation).
      USD-short (EURUSD long, GBPUSD long[, XAUUSD long]) / USD-long (EURUSD short, GBPUSD short[, XAUUSD short]).
    DXY is context only and never traded, so it carries no exposure of its own. "DXY-inverse exposure" is already
    what the USD groups count: EURUSD long + GBPUSD long are both short the dollar (= short DXY), so they share one cap.
    Indices are NOT put in a USD group: the index/dollar link flips sign by regime and is too loose for a hard cap;
    the 2% daily loss cap and the 5% drawdown lock still cover a bad day across both families.
  * Size = equity x risk% / (risk points x value per point per unit).

    python3 .claude/skills/risk-rules/scripts/position_size.py --risk-pts 29 [--equity 10000] [--value-per-point 1] \
        [--instrument EURUSD --direction short --open "GBPUSD:short"]
Exit codes: 0 ok, 1 group full (no new trade allowed), 2 usage error.
"""
from __future__ import annotations
import argparse, json, sys

STANDALONE, CORR_EACH, CORR_MAX, GROUP_MAX = 0.5, 0.25, 4, 1.0
USD_QUOTE = {"EURUSD", "GBPUSD", "XAUUSD"}  # long these = short USD (XAUUSD is switched off in config, kept here)
US_INDICES = {"US100", "US500", "NAS100"}   # NAS100 = the old OANDA name, so older journal entries still group


def correlation_group(instrument: str, direction: str) -> str:
    instrument = instrument.upper().split(":")[-1]
    if instrument in US_INDICES:
        return f"US-indices-{direction}"
    if instrument in USD_QUOTE:
        return "USD-short" if direction == "long" else "USD-long"
    return f"{instrument}-{direction}"


def _group_risk(same: list[tuple]) -> list[float]:
    """Risk % of each open trade in the group. A trade given as (instrument, direction, risk_pct) uses its recorded risk;
    a bare (instrument, direction) is assumed to have been sized by this tool in order: the first one standalone (0.5%),
    later ones 0.25%. Never assume the smaller number for a trade that was really sized bigger."""
    out = []
    for i, t in enumerate(same):
        if len(t) > 2 and t[2] is not None:
            out.append(float(t[2]))
        else:
            out.append(STANDALONE if i == 0 else CORR_EACH)
    return out


def risk_pct_for(instrument: str, direction: str, open_trades: list[tuple]) -> dict:
    """open_trades = [(instrument, direction[, risk_pct]), ...] currently open or pending.

    The group cap is checked against the risk the open trades REALLY carry. Before this fix it was checked as
    n x 0.25%, so a 0.5% standalone trade plus three 0.25% trades (= 1.25%) was reported as inside the 1% cap."""
    g = correlation_group(instrument, direction)
    same = [t for t in open_trades if correlation_group(t[0], t[1]) == g]
    used = round(sum(_group_risk(same)), 6)
    n = len(same) + 1
    if n == 1:
        return {"group": g, "risk_pct": STANDALONE, "mode": "standalone", "allowed": True, "group_used_pct": 0.0,
                "group_after_pct": STANDALONE, "note": ""}
    after = round(used + CORR_EACH, 6)
    if n > CORR_MAX or after > GROUP_MAX + 1e-9:
        why = f"max {CORR_MAX} trades" if n > CORR_MAX else f"{used:g}% + {CORR_EACH:g}% = {after:g}% would pass the {GROUP_MAX:g}% group cap"
        return {"group": g, "risk_pct": 0.0, "mode": "correlated", "allowed": False, "group_used_pct": used, "group_after_pct": used,
                "note": f"group {g} already has {len(same)} trade(s) carrying {used:g}%: {why} - no new trade"}
    return {"group": g, "risk_pct": CORR_EACH, "mode": "correlated", "allowed": True, "group_used_pct": used, "group_after_pct": after,
            "note": f"{n} correlated trades in {g}: new one {CORR_EACH:g}% (group {after:g}% of max {GROUP_MAX:g}%)."}


def size(risk_pct: float, risk_pts: float, equity: float | None = None, value_per_point: float = 1.0) -> dict:
    if risk_pts <= 0:
        raise ValueError("risk_pts must be > 0")
    formula = f"equity x {risk_pct / 100:g} / ({risk_pts:g} pts x value per point)"
    out = {"risk_pct": risk_pct, "risk_pts": risk_pts, "formula": formula}
    if equity is not None:
        cash = equity * risk_pct / 100
        out.update(equity=equity, risk_cash=round(cash, 2), units=round(cash / (risk_pts * value_per_point), 4))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--risk-pts", type=float, required=True); p.add_argument("--equity", type=float)
    p.add_argument("--value-per-point", type=float, default=1.0)
    p.add_argument("--instrument", default="US100"); p.add_argument("--direction", choices=["long", "short"], default="long")
    p.add_argument("--open", action="append", default=[], help="open/pending trade as INSTRUMENT:direction[:risk_pct] (repeatable)")
    a = p.parse_args(argv)
    try:
        opens = [(x.split(":")[0], x.split(":")[1], float(x.split(":")[2]) if len(x.split(":")) > 2 else None) for x in a.open]
        r = risk_pct_for(a.instrument, a.direction, opens)
        out = {**r, **(size(r["risk_pct"], a.risk_pts, a.equity, a.value_per_point) if r["allowed"] else {})}
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    print(json.dumps(out, indent=2))
    return 0 if r["allowed"] else 1


if __name__ == "__main__":
    sys.exit(main())
