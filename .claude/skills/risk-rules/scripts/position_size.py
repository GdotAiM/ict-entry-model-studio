#!/usr/bin/env python3
"""Risk % and position size. Never sizes a live order; it prints a formula and numbers for Hilda to check.

Risk rules:
  * Standalone trade: 0.5% of equity.
  * Correlated trades (same group): 0.25% each, max 4 open in the group, 1% group maximum.
    Groups: USD-short (EURUSD long, GBPUSD long, XAUUSD long), USD-long (EURUSD short, GBPUSD short, XAUUSD short),
    NAS100-long / NAS100-short (same-direction NAS trades are correlated with each other).
  * Size = equity x risk% / (risk points x value per point per unit).

    python3 .claude/skills/risk-rules/scripts/position_size.py --risk-pts 29 [--equity 10000] [--value-per-point 1] \
        [--instrument EURUSD --direction short --open "GBPUSD:short"]
Exit codes: 0 ok, 1 group full (no new trade allowed), 2 usage error.
"""
from __future__ import annotations
import argparse, json, sys

STANDALONE, CORR_EACH, CORR_MAX, GROUP_MAX = 0.5, 0.25, 4, 1.0
USD_QUOTE = {"EURUSD", "GBPUSD", "XAUUSD"}  # long these = short USD


def correlation_group(instrument: str, direction: str) -> str:
    if instrument in USD_QUOTE:
        return "USD-short" if direction == "long" else "USD-long"
    return f"{instrument}-{direction}"


def risk_pct_for(instrument: str, direction: str, open_trades: list[tuple[str, str]]) -> dict:
    """open_trades = [(instrument, direction), ...] currently open or pending."""
    g = correlation_group(instrument, direction)
    same = [t for t in open_trades if correlation_group(*t) == g]
    n = len(same) + 1
    if n == 1:
        return {"group": g, "risk_pct": STANDALONE, "mode": "standalone", "allowed": True, "note": ""}
    if n > CORR_MAX or n * CORR_EACH > GROUP_MAX + 1e-9:
        return {"group": g, "risk_pct": 0.0, "mode": "correlated", "allowed": False,
                "note": f"group {g} already has {len(same)} trades - max {CORR_MAX} / {GROUP_MAX}% reached, no new trade"}
    return {"group": g, "risk_pct": CORR_EACH, "mode": "correlated", "allowed": True,
            "note": f"{n} correlated trades in {g}: {CORR_EACH}% each (group {n * CORR_EACH:g}% of max {GROUP_MAX:g}%)."
                    + (" The open standalone trade was sized at 0.5%; flag that the group is now over-weight until it closes." if n == 2 else "")}


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
    p.add_argument("--instrument", default="NAS100"); p.add_argument("--direction", choices=["long", "short"], default="long")
    p.add_argument("--open", action="append", default=[], help="open/pending trade as INSTRUMENT:direction (repeatable)")
    a = p.parse_args(argv)
    try:
        opens = [tuple(x.split(":")) for x in a.open]
        r = risk_pct_for(a.instrument, a.direction, opens)
        out = {**r, **(size(r["risk_pct"], a.risk_pts, a.equity, a.value_per_point) if r["allowed"] else {})}
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    print(json.dumps(out, indent=2))
    return 0 if r["allowed"] else 1


if __name__ == "__main__":
    sys.exit(main())
