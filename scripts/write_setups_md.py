#!/usr/bin/env python3
"""Write findings/setups.md from state/setups/*.json. First line is always 'Updated: YYYY-MM-DD HH:MM SAST'.

    python3 scripts/write_setups_md.py [--state-dir state/setups] [--out findings/setups.md] [--watching "text" ...]
"""
from __future__ import annotations
import argparse, glob, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPEN = {"pending", "filled", "tp1"}


def header(now: datetime | None = None) -> str:
    now = (now or datetime.now(SAST)).astimezone(SAST)
    return f"Updated: {now:%Y-%m-%d %H:%M} SAST"


def line(s: dict) -> str:
    tps = ", ".join(f"{t['name']} {t['price']} ({t.get('r')}R{' PARTIAL' if t.get('partial') else ''})" for t in s.get("targets", []))
    ev = "; ".join(f"{e['event']} {e['time'][11:16]} {e.get('r', '')}R".rstrip() for e in s.get("events", []))
    return (f"- {s['instrument']} {s['direction'].upper()} {s['id']} {s.get('bias_label', '')} ({s.get('killzone', '')}) - "
            f"model: {s.get('model', '')}; stage: {s.get('charter_stage', '')}; entry {s['entry']}, stop {s['stop']}, "
            f"risk {s.get('risk_pts')}; {tps}. Status: {s.get('status', 'pending').upper()}" + (f" [{ev}]" if ev else "")
            + (f". Chart: {s['chart']}" if s.get("chart") else ""))


def build(setups: list[dict], watching: list[str], now: datetime | None = None, source: str = "") -> str:
    act = [s for s in setups if s.get("status", "pending") in OPEN]
    closed = [s for s in setups if s.get("status") not in OPEN]
    out = [header(now), "", "Entry model studio - setups. " + (f"Data: {source}. " if source else "") + "Analysis only, not financial advice.", "",
           "## Active setups"] + ([line(s) for s in act] or ["- none"])
    out += ["", "## Watching (Charter stage: Opportunity Discovery)"] + ([f"- {w}" for w in watching] or ["- none"])
    out += ["", "## Closed (recent)"] + ([line(s) for s in closed[-15:]] or ["- none"])
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", default=os.path.join(ROOT, "state", "setups"))
    p.add_argument("--out", default=os.path.join(ROOT, "findings", "setups.md"))
    p.add_argument("--watching", action="append", default=[]); p.add_argument("--source", default="")
    a = p.parse_args(argv)
    setups = []
    for f in sorted(glob.glob(os.path.join(a.state_dir, "*.json"))):
        with open(f) as fh:
            setups.append(json.load(fh))
    setups.sort(key=lambda s: s.get("alert_time", ""))
    text = build(setups, a.watching, source=a.source)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
