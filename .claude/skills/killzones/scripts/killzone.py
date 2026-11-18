#!/usr/bin/env python3
"""ICT kill zones: defined in New York time, reported in SAST (Africa/Johannesburg).

Deterministic and DST-correct (zoneinfo). Never convert by hand:
    python3 .claude/skills/killzones/scripts/killzone.py table --date 2026-11-12
    python3 .claude/skills/killzones/scripts/killzone.py now            # which kill zone is active (SAST clock)
    python3 .claude/skills/killzones/scripts/killzone.py at "2026-11-12 09:30"   # SAST wall time
    python3 .claude/skills/killzones/scripts/killzone.py ny 07:00 --date 2026-11-12   # NY -> SAST
Exit codes: 0 ok / in a watched kill zone, 1 not in a watched kill zone (for `now`/`at --watched`), 2 usage error.
"""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SAST = ZoneInfo("Africa/Johannesburg")

# (name, start NY, end NY). Start on the *previous* NY day when start > end (Asian crosses midnight).
KILLZONES = [
    ("Asian", "20:00", "00:00"),
    ("London Open", "02:00", "05:00"),
    ("NY AM", "07:00", "10:00"),
    ("Silver Bullet", "10:00", "11:00"),
    ("London Close", "10:00", "12:00"),
    ("NY PM", "13:30", "16:00"),
]
DEFAULT_WATCHED = ("London Open", "NY AM")  # Ntloso's cost-cut: only these two are scanned


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def ny_to_sast(hhmm: str, on: date) -> datetime:
    """NY wall time on NY date `on` -> aware SAST datetime."""
    return datetime.combine(on, _t(hhmm), NY).astimezone(SAST)


def offset_hours(on: date) -> int:
    """SAST minus NY in hours on that NY date at noon (6 in EDT, 7 in EST)."""
    noon = datetime.combine(on, time(12), NY)
    return int((noon.astimezone(SAST).utcoffset() - noon.utcoffset()).total_seconds() // 3600)


def killzones_for(trading_day: date) -> list[dict]:
    """Kill-zone windows for a NY trading day. Asian belongs to the evening before."""
    out = []
    for name, s, e in KILLZONES:
        if name == "Asian":
            start = datetime.combine(trading_day - timedelta(days=1), _t(s), NY)
            end = datetime.combine(trading_day, _t(e), NY)
        else:
            start = datetime.combine(trading_day, _t(s), NY)
            end = datetime.combine(trading_day, _t(e), NY)
        out.append({
            "name": name,
            "ny": f"{s}-{e}",
            "start_sast": start.astimezone(SAST),
            "end_sast": end.astimezone(SAST),
        })
    return out


def active_killzones(when: datetime) -> list[dict]:
    """All kill zones containing `when` (aware, or naive = SAST). Start inclusive, end exclusive."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=SAST)
    ny_day = when.astimezone(NY).date()
    hits = []
    for d in (ny_day, ny_day + timedelta(days=1)):  # Asian of the next trading day starts this NY evening
        for kz in killzones_for(d):
            if kz["start_sast"] <= when < kz["end_sast"]:
                hits.append(kz)
    return hits


def in_watched_killzone(when: datetime, watched=DEFAULT_WATCHED) -> dict | None:
    for kz in active_killzones(when):
        if kz["name"] in watched:
            return kz
    return None


def killzone_end(name: str, trading_day: date) -> datetime:
    for kz in killzones_for(trading_day):
        if kz["name"] == name:
            return kz["end_sast"]
    raise KeyError(name)


def _fmt(kz: dict) -> dict:
    return {"name": kz["name"], "ny": kz["ny"],
            "sast": f'{kz["start_sast"]:%H:%M}-{kz["end_sast"]:%H:%M}',
            "start_sast": kz["start_sast"].isoformat(), "end_sast": kz["end_sast"].isoformat()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("table"); t.add_argument("--date", required=True)
    n = sub.add_parser("now"); n.add_argument("--watched", default=",".join(DEFAULT_WATCHED))
    a = sub.add_parser("at"); a.add_argument("when", help="SAST wall time 'YYYY-MM-DD HH:MM'"); a.add_argument("--watched", default=",".join(DEFAULT_WATCHED))
    y = sub.add_parser("ny"); y.add_argument("times", nargs="+"); y.add_argument("--date", required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "table":
            d = date.fromisoformat(args.date)
            print(json.dumps({"date": args.date, "sast_minus_ny_hours": offset_hours(d),
                              "killzones": [_fmt(k) for k in killzones_for(d)]}, indent=2))
            return 0
        if args.cmd == "ny":
            d = date.fromisoformat(args.date)
            for s in args.times:
                print(f"{s} NY = {ny_to_sast(s, d):%H:%M} SAST ({args.date}, NY+{offset_hours(d)})")
            return 0
        when = datetime.now(SAST) if args.cmd == "now" else datetime.strptime(args.when, "%Y-%m-%d %H:%M").replace(tzinfo=SAST)
        watched = tuple(x.strip() for x in args.watched.split(",") if x.strip())
        act = active_killzones(when)
        kz = in_watched_killzone(when, watched)
        print(json.dumps({"sast": when.strftime("%Y-%m-%d %H:%M"), "active": [_fmt(k) for k in act],
                          "watched_active": _fmt(kz) if kz else None}, indent=2))
        return 0 if kz else 1
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
