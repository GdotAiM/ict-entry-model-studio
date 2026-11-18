#!/usr/bin/env python3
"""Alert dedupe log (CSV). A row in this file means the alert WAS delivered to Hilda.

Never assume an earlier alert was aborted or undelivered, and never send an alert or status that already has
a row here. Only events with no matching (setup_id, event) row may be sent.

    python3 scripts/alert_log.py check --id N6 --event NEW          # exit 1 if already logged
    python3 scripts/alert_log.py add --id N6 --event NEW --instrument NAS100 --direction short \
        --session "London Open" --time 10:51 --swept "..." --mss "..." --zone "..." --stop 28248.0 \
        --target "28159.4 / 28108.7" --r 0 --chart charts/nas_N6_new.png
    python3 scripts/alert_log.py list [--date 2026-11-11]
Log a row only AFTER the alert is delivered. Columns extend the original kz_alerts.csv schema
(date,session,instrument,direction,alert_time_sast,swept,mss,zone,stop,target) with setup_id, label
(with-bias | COUNTER-TREND), model, event, r and chart.
Exit codes: 0 ok / not yet logged, 1 duplicate, 2 usage error.
"""
from __future__ import annotations
import argparse, csv, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_LOG = os.path.join(ROOT, "state", "alerts_log.csv")
FIELDS = ["date", "session", "instrument", "setup_id", "direction", "label", "model", "event", "alert_time_sast",
          "swept", "mss", "zone", "stop", "target", "r", "chart"]
EVENTS = {"NEW", "FILLED", "TP1", "TP2", "TP3", "STOP", "BE", "VOID", "INVALID", "CANCELLED", "UPDATE", "LOCKED"}


def n_targets(row: dict) -> int:
    """Number of targets a log row carries (the `target` column is 'p1 / p2 / ...'). 0 if empty."""
    return len([t for t in (row.get("target") or "").split("/") if t.strip()])


def read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def is_logged(path: str, setup_id: str, event: str) -> bool:
    return any(r["setup_id"] == setup_id and r["event"] == event.upper() for r in read(path))


def add(path: str, row: dict) -> bool:
    """Append if (setup_id, event) is new. Returns False on duplicate. UPDATE rows dedupe on their full text."""
    row = {k: ("" if row.get(k) is None else str(row.get(k))) for k in FIELDS}
    row["event"] = row["event"].upper()
    if row["event"] not in EVENTS:
        raise ValueError(f"event must be one of {sorted(EVENTS)}")
    if not row["setup_id"]:
        raise ValueError("setup_id required")
    rows = read(path)
    if row["event"] == "UPDATE":
        if any(r["setup_id"] == row["setup_id"] and r["event"] == "UPDATE" and r["mss"] == row["mss"] for r in rows):
            return False
    elif any(r["setup_id"] == row["setup_id"] and r["event"] == row["event"] for r in rows):
        return False
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default=DEFAULT_LOG)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--id", required=True); c.add_argument("--event", required=True)
    a = sub.add_parser("add")
    a.add_argument("--id", required=True); a.add_argument("--event", required=True)
    for k in ("date", "session", "instrument", "direction", "label", "model", "time", "swept", "mss", "zone", "stop", "target", "r", "chart"):
        a.add_argument(f"--{k}", default="")
    l = sub.add_parser("list"); l.add_argument("--date")
    args = p.parse_args(argv)
    if args.cmd == "check":
        if is_logged(args.log, args.id, args.event):
            print(f"ALREADY LOGGED (delivered): {args.id} {args.event.upper()} - do not resend"); return 1
        print(f"not logged: {args.id} {args.event.upper()} - may be sent"); return 0
    if args.cmd == "add":
        now = datetime.now(SAST)
        row = {"date": args.date or now.strftime("%Y-%m-%d"), "session": args.session, "instrument": args.instrument,
               "setup_id": args.id, "direction": args.direction, "label": args.label, "model": args.model, "event": args.event,
               "alert_time_sast": args.time or now.strftime("%H:%M"), "swept": args.swept, "mss": args.mss,
               "zone": args.zone, "stop": args.stop, "target": args.target, "r": args.r, "chart": args.chart}
        try:
            ok = add(args.log, row)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr); return 2
        print("logged" if ok else f"DUPLICATE: {args.id} {args.event.upper()} already logged - not added")
        return 0 if ok else 1
    rows = [r for r in read(args.log) if not args.date or r["date"] == args.date]
    w = csv.DictWriter(sys.stdout, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
