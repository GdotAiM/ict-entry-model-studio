#!/usr/bin/env python3
"""Setup lifecycle tracker: replays new bars against a setup and emits status-change events.

States: pending -> filled -> tp1 (partial banked, rest open) -> closed_tp2 | closed_stop | closed_be
        pending -> void (TP1 traded before the fill) | invalid (raid extreme broke before the fill)
                | cancelled (kill zone ended unfilled: decided by the clock, `now` >= kill-zone end, so it does not
                  wait for a bar that opens after the end - that bar is still forming when the close-out run fires)
Conservative intrabar rules (1m bars cannot tell the order of high and low):
  * A bar that reaches the stop and a target -> STOP is assumed.
  * A pending bar that touches the entry AND TP1 -> VOID (no win is claimed).
  * A pending bar that opens beyond the raid extreme without having filled -> INVALID.
  * A pending bar that touches the entry and then trades through the raid extreme -> FILLED, then the
    stop is checked on the same bar.
  * A bar that OPENS beyond the stop -> stop filled at the open (gap), R worse than -1.

    python3 scripts/lifecycle.py --setup state/setups/N6.json --bars drafts/bars/NAS100_1m.csv [--write]
Prints {"status", "events": [...]} for events newer than setup["last_checked"]. --write updates the setup file.
Exit codes: 0 new events, 1 no change, 2 usage error.
"""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import datetime
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")

# "locked": found while the daily-loss or drawdown cap was hit (lib/riskstate.py); never alerted, never tracked.
TERMINAL = {"closed_tp2", "closed_tp1", "closed_stop", "closed_be", "void", "invalid", "cancelled", "locked"}


def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return [{"time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in csv.DictReader(f)]


def _aware(t: str | datetime) -> datetime:
    d = datetime.fromisoformat(t) if isinstance(t, str) else t
    return d if d.tzinfo else d.replace(tzinfo=SAST)


def evaluate(setup: dict, bars: list[dict], kz_end: str | None = None,
             now: str | datetime | None = None) -> tuple[str, list[dict]]:
    """Return (new_status, events). Pure function; does not mutate `setup`.
    `now` (aware datetime or ISO string): when given and the kill zone has ended by `now`, a setup still pending after
    the closed bars are replayed is CANCELLED at the kill-zone end time, even if no bar after the end exists yet."""
    status = setup.get("status", "pending")
    if status in TERMINAL:
        return status, []
    short = setup["direction"] == "short"
    entry, stop, extreme = setup["entry"], setup["stop"], setup["sweep"]["extreme"]
    tps = [t["price"] for t in setup["targets"]]
    risk = abs(stop - entry)
    after = setup.get("last_checked") or setup.get("alert_time") or ""
    kz_end = kz_end or setup.get("killzone_end")
    tp_hit = sum(1 for e in setup.get("events", []) if e["event"].startswith("TP"))
    be = setup.get("move_to_be_after_tp1", False)
    events: list[dict] = []

    def beyond(price, level):  # adverse side for the trade (above for shorts)
        return price >= level if short else price <= level

    def through(price, level):  # profit side
        return price <= level if short else price >= level

    def r_at(price):
        return round(((entry - price) if short else (price - entry)) / risk, 2)

    for b in bars:
        if b["time"] <= after:
            continue
        adverse, favour = (b["high"], b["low"]) if short else (b["low"], b["high"])
        if status == "pending":
            if kz_end and _aware(b["time"]) >= _aware(kz_end):
                events.append({"event": "CANCELLED", "time": b["time"], "price": None, "r": 0.0,
                               "note": "kill zone ended unfilled"}); status = "cancelled"; break
            touched = beyond(adverse, entry)
            if beyond(b["open"], extreme) or (not touched and beyond(adverse, extreme)):
                events.append({"event": "INVALID", "time": b["time"], "price": adverse, "r": 0.0,
                               "note": f"raid extreme {extreme} broke before the fill"}); status = "invalid"; break
            if through(favour, tps[0]):
                events.append({"event": "VOID", "time": b["time"], "price": favour, "r": 0.0,
                               "note": f"TP1 {tps[0]} traded before the fill" + (" (same bar as entry touch - ambiguous, treated as void)" if touched else "")})
                status = "void"; break
            if touched:
                events.append({"event": "FILLED", "time": b["time"], "price": entry, "r": 0.0, "note": ""})
                status = "filled"
                if beyond(adverse, stop):
                    events.append({"event": "STOP", "time": b["time"], "price": stop, "r": -1.0,
                                   "note": "stopped on the fill bar"}); status = "closed_stop"; break
            continue
        # filled / tp1: open trade management
        cur_stop = entry if (be and tp_hit >= 1) else stop
        if beyond(b["open"], cur_stop):
            px = b["open"]
            ev = "BE" if cur_stop == entry else "STOP"
            events.append({"event": ev, "time": b["time"], "price": px, "r": r_at(px),
                           "note": "gap through stop - filled at the open"})
            status = "closed_be" if ev == "BE" else "closed_stop"; break
        if beyond(adverse, cur_stop):
            ev = "BE" if cur_stop == entry else "STOP"
            events.append({"event": ev, "time": b["time"], "price": cur_stop, "r": 0.0 if ev == "BE" else -1.0,
                           "note": "remaining position" if tp_hit else ""})
            status = "closed_be" if ev == "BE" else "closed_stop"; break
        while tp_hit < len(tps) and through(favour, tps[tp_hit]):
            tp_hit += 1
            events.append({"event": f"TP{tp_hit}", "time": b["time"], "price": tps[tp_hit - 1],
                           "r": r_at(tps[tp_hit - 1]), "note": ""})
        if tp_hit >= len(tps):
            status = f"closed_tp{len(tps)}"; break
        if tp_hit:
            status = "tp1"
    if status == "pending" and kz_end and now is not None and _aware(now) >= _aware(kz_end):
        # every closed bar inside the kill zone has been checked above and none filled the setup
        events.append({"event": "CANCELLED", "time": _aware(kz_end).astimezone(SAST).isoformat(), "price": None,
                       "r": 0.0, "note": "kill zone ended unfilled"})
        status = "cancelled"
    return status, events


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", required=True); p.add_argument("--bars", required=True)
    p.add_argument("--kz-end", help="ISO time the kill zone ends (cancel unfilled at/after it)")
    p.add_argument("--now", help="ISO time of this check (default: the current time); an unfilled setup is cancelled "
                                  "once now >= the kill-zone end")
    p.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    with open(a.setup) as f:
        setup = json.load(f)
    bars = load_csv(a.bars)
    try:
        status, events = evaluate(setup, bars, a.kz_end, a.now or datetime.now(SAST))
    except (KeyError, IndexError) as e:
        print(f"error: setup missing field {e}", file=sys.stderr); return 2
    print(json.dumps({"id": setup.get("id"), "status": status, "events": events}, indent=2))
    if a.write:
        setup["status"] = status
        setup.setdefault("events", []).extend(events)
        if bars:
            setup["last_checked"] = bars[-1]["time"]
        with open(a.setup, "w") as f:
            json.dump(setup, f, indent=2)
    return 0 if events else 1


if __name__ == "__main__":
    sys.exit(main())
