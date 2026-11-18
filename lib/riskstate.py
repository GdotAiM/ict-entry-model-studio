"""Account risk state from the setup records: realised P&L, drawdown, today's loss, open risk and the LOCKED flag.

There is no broker link (analysis only), so the "account" is the model account: every alerted setup is counted as if
it was taken at the risk % in its record (`risk_pct`), with the same partial convention as scripts/recap.py
(TP1 banks `partial_share` of the position when there is a second target). Realised % compounds on equity.

Caps come from config/instruments.json -> risk:
  standalone_pct 0.5 (per trade), daily_loss_cap_pct 2.0, max_drawdown_pct 5.0, correlated_group_max_pct 1.0.

  * Daily loss: net realised % for the current trading day (17:00 New York roll, DST-correct). At -2% -> LOCKED for
    the rest of that trading day.
  * Drawdown: (peak equity - equity) / peak since `risk.dd_reset_at` (or since the first record). At 5% -> LOCKED
    until you review and set `risk.dd_reset_at` (an ISO date-time) in config/instruments.local.json.
  * Pre-trade headroom: a new setup is only allowed if today's net realised % minus all open risk (pending, filled,
    and the remaining part after TP1) minus the new risk stays within the daily cap, and the same worst case keeps
    drawdown within the max. So a run of stops can never take you past either cap.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SAST, NY = ZoneInfo("Africa/Johannesburg"), ZoneInfo("America/New_York")
OPEN = {"pending", "filled", "tp1"}
CLOSE = {"TP2", "TP3", "STOP", "BE"}
EPS = 1e-9
DEFAULTS = {"standalone_pct": 0.5, "daily_loss_cap_pct": 2.0, "max_drawdown_pct": 5.0, "correlated_group_max_pct": 1.0,
            "partial_share": 0.5, "dd_reset_at": None}


def caps(cfg: dict | None) -> dict:
    r = dict(DEFAULTS)
    r.update({k: v for k, v in ((cfg or {}).get("risk") or {}).items() if k in DEFAULTS})
    return r


def trading_day(t: datetime) -> date:
    """Trading day of a moment: the session that starts 17:00 New York the evening before."""
    ny = t.astimezone(NY)
    return ny.date() + timedelta(days=1) if ny.hour >= 17 else ny.date()


def trading_day_end(td: date) -> datetime:
    return datetime.combine(td, datetime.min.time().replace(hour=17), NY).astimezone(SAST)


def realised(setup: dict, partial_share: float = 0.5) -> list[tuple[datetime, float, str]]:
    """[(time, pct of equity, event)] booked by a setup's events."""
    risk = float(setup.get("risk_pct") or 0)
    n_tp = len(setup.get("targets") or [])
    out, tp1 = [], False
    for e in setup.get("events", []):
        ev, r = e.get("event"), e.get("r")
        if r is None or not e.get("time"):
            continue
        t = datetime.fromisoformat(e["time"])
        if ev == "TP1":
            share = partial_share if n_tp >= 2 else 1.0
            tp1 = n_tp >= 2
            out.append((t, share * float(r) * risk, ev))
        elif ev in CLOSE:
            share = (1 - partial_share) if tp1 else 1.0
            out.append((t, share * float(r) * risk, ev))
    return out


def open_risk(setup: dict, partial_share: float = 0.5) -> float:
    st = setup.get("status")
    if st not in OPEN:
        return 0.0
    risk = float(setup.get("risk_pct") or 0)
    return risk * (1 - partial_share) if st == "tp1" and len(setup.get("targets") or []) >= 2 else risk


def risk_state(setups: list[dict], now: datetime, cfg: dict | None = None) -> dict:
    c = caps(cfg)
    ps = c["partial_share"]
    reset = datetime.fromisoformat(c["dd_reset_at"]) if c["dd_reset_at"] else None
    if reset and reset.tzinfo is None:
        reset = reset.replace(tzinfo=SAST)
    book = sorted((x for s in setups for x in realised(s, ps) if x[0] <= now), key=lambda x: x[0])
    eq, peak, eq_dd, peak_dd, dd_hit_at = 1.0, 1.0, 1.0, 1.0, None
    for t, pct, _ in book:
        eq *= 1 + pct / 100
        peak = max(peak, eq)
        if reset is None or t >= reset:
            eq_dd *= 1 + pct / 100
            peak_dd = max(peak_dd, eq_dd)
            dd_now = 100 * (peak_dd - eq_dd) / peak_dd
            if dd_now >= c["max_drawdown_pct"] - EPS and dd_hit_at is None:
                dd_hit_at = t
            elif dd_now < c["max_drawdown_pct"] - EPS:
                dd_hit_at = None
    td = trading_day(now)
    day = round(sum(p for t, p, _ in book if trading_day(t) == td), 6)
    dd = round(100 * (peak_dd - eq_dd) / peak_dd, 6)
    opn = [s for s in setups if s.get("status") in OPEN]
    orisk = round(sum(open_risk(s, ps) for s in opn), 6)
    daily_hit = day <= -c["daily_loss_cap_pct"] + EPS
    dd_hit = dd >= c["max_drawdown_pct"] - EPS
    lock = None
    if dd_hit:
        lock = {"kind": "drawdown", "id": f"RISK-DD-{(dd_hit_at or now).astimezone(SAST):%Y%m%d}",
                "reason": f"max drawdown {c['max_drawdown_pct']:g}% reached ({dd:.2f}% from peak)",
                "until": "you review and set risk.dd_reset_at in config/instruments.local.json"}
    elif daily_hit:
        lock = {"kind": "daily", "id": f"RISK-DAY-{td:%Y%m%d}",
                "reason": f"daily loss cap {c['daily_loss_cap_pct']:g}% reached ({day:+.2f}% today)",
                "until": f"{trading_day_end(td):%H:%M} SAST ({trading_day_end(td):%a %d %b}, next trading day at 17:00 New York)"}
    return {"now": now.isoformat(), "trading_day": td.isoformat(), "caps": c,
            "equity_pct": round(100 * (eq - 1), 4), "peak_pct": round(100 * (peak - 1), 4),
            "daily_pct": day, "daily_used_pct": round(max(0.0, -day), 6), "drawdown_pct": dd,
            "open_risk_pct": orisk, "open_setups": [{"id": s["id"], "instrument": s["instrument"], "direction": s["direction"],
                                                     "status": s["status"], "risk_pct": open_risk(s, ps)} for s in opn],
            "locked": lock is not None, "lock": lock, "realised_events": len(book)}


def headroom(rs: dict, new_risk: float) -> dict:
    """Would a new setup at `new_risk` % keep the worst case inside the daily and drawdown caps?"""
    c = rs["caps"]
    worst_day = rs["daily_pct"] - rs["open_risk_pct"] - new_risk
    worst_dd = rs["drawdown_pct"] + rs["open_risk_pct"] + new_risk  # additive upper bound, conservative
    why = []
    if rs["locked"]:
        why.append(f"LOCKED: {rs['lock']['reason']}")
    if worst_day < -c["daily_loss_cap_pct"] - EPS:
        why.append(f"daily worst case {worst_day:+.2f}% would pass the {c['daily_loss_cap_pct']:g}% daily cap")
    if worst_dd > c["max_drawdown_pct"] + EPS:
        why.append(f"drawdown worst case {worst_dd:.2f}% would pass the {c['max_drawdown_pct']:g}% max")
    return {"allowed": not why, "reasons": why, "daily_left_pct": round(c["daily_loss_cap_pct"] + rs["daily_pct"] - rs["open_risk_pct"], 4),
            "dd_left_pct": round(c["max_drawdown_pct"] - rs["drawdown_pct"] - rs["open_risk_pct"], 4)}
