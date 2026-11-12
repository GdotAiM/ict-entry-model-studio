#!/usr/bin/env python3
"""Scheduler for the kill-zone watch: every ~6 minutes, only inside the watched kill zones (default London Open and
NY AM), computed in New York time and reported in SAST, DST-correct. One extra close-out run just after each kill zone
ends cancels unfilled setups. Weekends (NY) are skipped.

    python3 scripts/scheduler.py windows [--date 2026-11-12]      # today's watched windows in NY and SAST
    python3 scripts/scheduler.py next [--count 8]                  # next run times (SAST)
    python3 scripts/scheduler.py tick [--runner python|claude] [--dry-run]   # for cron / systemd / Task Scheduler:
                                                                   # runs one cycle if this minute is due, else exits 0 silently
    python3 scripts/scheduler.py loop [--runner python|claude]     # long-running alternative to cron
Runners:
  python (default) - scripts/watch.py --quiet: pure Python, no Claude needed.
  claude           - `claude -p "/kz-watch"` (+ $CLAUDE_ARGS), lets Claude Code run the agent pipeline.
A lock file (state/.watch.lock) prevents overlapping runs; a lock older than 15 minutes is treated as stale.
"""
from __future__ import annotations
import argparse, json, os, shlex, subprocess, sys, time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "skills", "killzones", "scripts"))
import emconfig, killzone  # noqa: E402

SAST, NY = ZoneInfo("Africa/Johannesburg"), ZoneInfo("America/New_York")
LOCK = os.path.join(ROOT, "state", ".watch.lock")
# Close-out at the kill-zone end minute (end is exclusive). The bar that opens at the end minute is still forming then and
# is never read (closed bars only); lifecycle.evaluate cancels unfilled setups by the clock (now >= end) instead.
CLOSE_OUT = timedelta(minutes=0)
LAST = os.path.join(ROOT, "state", ".last_slot")


def settings() -> tuple[tuple[str, ...], int]:
    cfg = emconfig.load_config()
    return tuple(cfg.get("watch_killzones", killzone.DEFAULT_WATCHED)), int(cfg.get("scan_interval_minutes", 6))


def windows(trading_day: date, watched) -> list[dict]:
    if trading_day.weekday() >= 5:
        return []
    return [k for k in killzone.killzones_for(trading_day) if k["name"] in watched]


def slots(trading_day: date, watched, every: int) -> list[tuple[datetime, str]]:
    out = []
    for k in windows(trading_day, watched):
        t = k["start_sast"]
        while t < k["end_sast"]:
            out.append((t, k["name"])); t += timedelta(minutes=every)
        out.append((k["end_sast"] + CLOSE_OUT, f"{k['name']} close-out"))
    return sorted(out)


def due_slot(now: datetime, watched, every: int, tolerance_min: int = 2) -> tuple[datetime, str] | None:
    """Slot due at `now` (now within [slot, slot + tolerance + 1 min)), else None. Uses the NY trading day, so the SAST
    times move by an hour when the US changes clocks."""
    now = now.astimezone(SAST)
    for d in sorted({now.astimezone(NY).date(), now.astimezone(NY).date() + timedelta(days=1)}):
        for t, name in slots(d, watched, every):
            if t <= now < t + timedelta(minutes=tolerance_min + 1):
                return t, name
    return None


def is_due(now: datetime, watched, every: int, tolerance_min: int = 2) -> str | None:
    s = due_slot(now, watched, every, tolerance_min)
    return s[1] if s else None


def next_runs(now: datetime, watched, every: int, count: int = 8) -> list[dict]:
    out, d = [], now.astimezone(NY).date()
    for _ in range(10):
        for t, name in slots(d, watched, every):
            if t >= now and len(out) < count:
                out.append({"sast": t.strftime("%a %Y-%m-%d %H:%M"), "ny": t.astimezone(NY).strftime("%H:%M"), "slot": name})
        if len(out) >= count:
            break
        d += timedelta(days=1)
    return out


def _lock(now: datetime) -> bool:
    if os.path.exists(LOCK):
        try:
            started = datetime.fromisoformat(open(LOCK).read().split()[1])
            if now - started < timedelta(minutes=15):
                return False
        except (IndexError, ValueError):
            pass
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with open(LOCK, "w") as f:
        f.write(f"{os.getpid()} {now.isoformat()}")
    return True


def _unlock():
    try:
        os.remove(LOCK)
    except FileNotFoundError:
        pass


def runner_cmd(runner: str, dry_run: bool = False, env=os.environ) -> list[str]:
    if runner == "claude":
        return ["claude", "-p", "/kz-watch", *shlex.split(env.get("CLAUDE_ARGS", ""))]
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "watch.py"), "--quiet"]
    return cmd + (["--dry-run"] if dry_run else [])


def tick(now: datetime, runner: str = "python", dry_run: bool = False, run=subprocess.run) -> dict:
    watched, every = settings()
    due = due_slot(now, watched, every)
    if not due:
        return {"ran": False, "reason": "not due (outside watched kill zones or between slots)"}
    slot_t, slot = due
    if os.path.exists(LAST) and open(LAST).read().strip() == slot_t.isoformat():
        return {"ran": False, "reason": "slot already run (cron fired more than once)"}
    if not _lock(now):
        return {"ran": False, "reason": "previous run still active (lock)"}
    try:
        cmd = runner_cmd(runner, dry_run)
        with open(LAST, "w") as f:
            f.write(slot_t.isoformat())
        p = run(cmd, cwd=ROOT, timeout=600)
        return {"ran": True, "slot": slot, "sast": now.strftime("%Y-%m-%d %H:%M"), "cmd": cmd[:3], "returncode": p.returncode}
    finally:
        _unlock()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("windows"); w.add_argument("--date")
    n = sub.add_parser("next"); n.add_argument("--count", type=int, default=8)
    for name in ("tick", "loop"):
        x = sub.add_parser(name)
        x.add_argument("--runner", choices=["python", "claude"], default=os.environ.get("WATCH_RUNNER", "python"))
        x.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    watched, every = settings()
    now = datetime.now(SAST)
    if a.cmd == "windows":
        d = date.fromisoformat(a.date) if a.date else now.astimezone(NY).date()
        print(json.dumps({"ny_trading_day": d.isoformat(), "sast_minus_ny_hours": killzone.offset_hours(d), "every_min": every,
                          "windows": [{"name": k["name"], "ny": k["ny"], "sast": f"{k['start_sast']:%H:%M}-{k['end_sast']:%H:%M}"}
                                      for k in windows(d, watched)]}, indent=2))
        return 0
    if a.cmd == "next":
        print(json.dumps(next_runs(now, watched, every, a.count), indent=2)); return 0
    if a.cmd == "tick":
        res = tick(now, a.runner, a.dry_run)
        if res["ran"]:
            print(json.dumps(res))
        return 0 if not res.get("returncode") else res["returncode"]
    print(f"scheduler loop started {now:%Y-%m-%d %H:%M} SAST; next: {next_runs(now, watched, every, 1)}", flush=True)
    while True:  # pragma: no cover - long-running
        res = tick(datetime.now(SAST), a.runner, a.dry_run)
        if res["ran"]:
            print(json.dumps(res), flush=True)
        time.sleep(60 - datetime.now().second + 1)


if __name__ == "__main__":
    sys.exit(main())
