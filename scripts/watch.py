#!/usr/bin/env python3
"""One watch cycle, pure Python (no Claude needed). The scheduler runs this every ~6 minutes in the watched kill zones.

Cycle:
  1. Lifecycle: replay new 1m bars against every pending/open setup (fill, TP1/TP2, stop, BE, void, invalid, cancel).
  2. Scan (only inside a watched kill zone): liquidity levels -> sweep -> MSS with displacement -> FVG -> OTE,
     Unicorn + Charter model detection, multi-timeframe bias label, stop buffer + 1:2 validation, correlated sizing.
  3. Outbox: every setup event without a row in state/alerts_log.csv is rendered, gated (check_alert.py), charted and
     delivered (notify.py). The log row is written only after a successful delivery.
  4. findings/setups.md is refreshed. Nothing is sent when nothing changed.
Risk caps (lib/riskstate.py): after step 1 the account state is rebuilt from the setups. At the 2% daily loss or 5% drawdown
cap the cycle is LOCKED: setups found are saved as "locked" (never alerted) and ONE "RISK LOCKED" alert goes out per lock.
Otherwise a setup that would let the worst case (today's result - all open risk - its risk) pass a cap is skipped.

    python3 scripts/watch.py                       # live: TradingView (yfinance fallback, disclosed)
    python3 scripts/watch.py --dry-run             # everything except sending / logging
    python3 scripts/watch.py --bars-dir DIR --now "2026-01-15T09:27:00+02:00" --dry-run   # offline replay
Exit codes: 0 ok, 1 delivery failures, 3 no data for every instrument.
"""
from __future__ import annotations
import argparse, csv, glob, json, os, sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SK = os.path.join(ROOT, ".claude", "skills")
for p in ("lib", "scripts", *[os.path.join(".claude", "skills", s, "scripts") for s in
          ("killzones", "market-data", "ict-bias", "liquidity", "setup-detection", "risk-rules", "alert-format", "chart-markup")]):
    sys.path.insert(0, os.path.join(ROOT, p))

import emconfig, riskstate, killzone, fetch_bars, mtf_bias, liquidity_levels, detect, models  # noqa: E402
import validate_setup, position_size, render_alert, check_alert, lifecycle, alert_log, notify, write_setups_md  # noqa: E402

SAST, NY = ZoneInfo("Africa/Johannesburg"), ZoneInfo("America/New_York")
PREFIX = {"US100": "N", "US500": "S", "XAUUSD": "X", "EURUSD": "E", "GBPUSD": "G"}  # N kept for US100 (ex-NAS100) so old ids line up
OPEN = {"pending", "filled", "tp1"}
INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
LEVEL_RANK = ["PWH", "PWL", "PMH", "PML", "PDH", "PDL", "week", "Asia", "old", "equal", "KZ", "swing"]


# ---------------------------------------------------------------- data
class Bars:
    """Bar source: live fetch (TradingView -> yfinance fallback) or a directory of <INST>_<iv>.csv files."""

    def __init__(self, bars_dir: str | None, now: datetime, cfg: dict, n1m: int = 1000):
        self.dir, self.now, self.cfg, self.n1m, self.cache = bars_dir, now, cfg, n1m, {}
        self.blocked: dict[tuple[str, str], LookupError] = {}

    def get(self, inst: str, iv: str):
        if (inst, iv) in self.blocked:
            raise self.blocked[(inst, iv)]
        if (inst, iv) in self.cache:
            return self.cache[(inst, iv)]
        if self.dir:
            path = os.path.join(self.dir, f"{inst}_{iv}.csv")
            if not os.path.exists(path):
                raise LookupError(f"NO DATA: {path} missing")
            with open(path, newline="") as f:
                rows = [{"time": r["time"], **{k: float(r[k]) for k in ("open", "high", "low", "close")}} for r in csv.DictReader(f)]
            meta = {"source": f"local file {os.path.basename(path)}", "fallback": False}
            if os.path.exists(path + ".meta.json"):
                meta = json.load(open(path + ".meta.json"))
        else:
            rows, meta = fetch_bars.fetch(inst, iv, self.n1m if iv == "1m" else 400, self.cfg)
        if meta.get("fallback") and self.cfg["instruments"][inst].get("block_on_fallback"):
            # yfinance proxies for US100 / XAUUSD are futures (NQ=F, GC=F): their basis to the OANDA CFD can exceed the
            # stop buffer, so levels, fills and stops computed on them are not trustworthy. Treated as NO DATA.
            err = LookupError(f"NO DATA for {inst} {iv}: TradingView failed and the yfinance fallback ({meta.get('source')}) "
                              f"is a futures proxy blocked for {inst} (block_on_fallback; set it to false in "
                              f"config/instruments.local.json to allow a disclosed fallback)")
            self.blocked[(inst, iv)] = err
            raise err
        if iv == "1d":
            rows = self._with_forming_bar(inst, rows)
        else:
            rows = self._closed(rows, iv)
        if not rows:
            raise LookupError(f"NO DATA for {inst} {iv} up to {self.now.isoformat()}")
        self.cache[(inst, iv)] = (rows, meta)
        return rows, meta


    def _closed(self, rows: list[dict], iv: str) -> list[dict]:
        """Only bars that have CLOSED by `now` (open time + interval <= now). The newest bar from a live feed is usually
        still forming: its high/low/close are incomplete and change after the cycle, and lifecycle.py never re-reads a bar
        at or before `last_checked`, so a stop or fill in the rest of that minute would be missed."""
        step = timedelta(minutes=INTERVAL_MIN[iv])
        return [r for r in rows if datetime.fromisoformat(r["time"]) + step <= self.now]

    def trading_day(self) -> date:
        ny = self.now.astimezone(NY)
        return ny.date() + timedelta(days=1) if ny.hour >= 17 else ny.date()

    def _with_forming_bar(self, inst: str, rows: list[dict]) -> list[dict]:
        """Closed daily bars before today's trading date + today's forming bar rebuilt from 1m bars up to `now`.
        Same result live, and no look-ahead when replaying history with --now."""
        td = self.trading_day()
        closed = [r for r in rows if r["time"][:10] < td.isoformat()]
        try:
            m1 = self.get(inst, "1m")[0]
        except LookupError:
            return closed + [r for r in rows if r["time"][:10] == td.isoformat()][:1]
        start = datetime.combine(td - timedelta(days=1), datetime.min.time().replace(hour=17), NY).astimezone(SAST).isoformat()
        today = [b for b in m1 if b["time"] >= start]
        if today:
            closed.append({"time": td.isoformat(), "open": today[0]["open"], "high": max(b["high"] for b in today),
                           "low": min(b["low"] for b in today), "close": today[-1]["close"]})
        return closed


# ---------------------------------------------------------------- state
class State:
    def __init__(self, root: str):
        self.root = root
        self.setups_dir = os.path.join(root, "state", "setups")
        self.log = os.path.join(root, "state", "alerts_log.csv")
        os.makedirs(self.setups_dir, exist_ok=True)

    def setups(self) -> list[dict]:
        out = []
        for f in sorted(glob.glob(os.path.join(self.setups_dir, "*.json"))):
            with open(f) as fh:
                out.append(json.load(fh))
        return out

    def save(self, s: dict) -> None:
        with open(os.path.join(self.setups_dir, f"{s['id']}.json"), "w") as f:
            json.dump(s, f, indent=2)

    def next_id(self, inst: str) -> str:
        p = PREFIX[inst]
        used = [s["id"] for s in self.setups()] + [r["setup_id"] for r in alert_log.read(self.log)]
        nums = [int(x[len(p):]) for x in used if x.startswith(p) and x[len(p):].isdigit()]
        return f"{p}{max(nums, default=0) + 1}"


# ---------------------------------------------------------------- bias
def bias_for(st: State, bars: Bars, inst: str, today: str, last: float) -> dict:
    path = os.path.join(st.root, "state", f"bias_{today}.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    if inst not in cache:
        daily, meta = bars.get(inst, "1d")
        dxy = None
        if bars.cfg["instruments"][inst].get("usd_leg") == "quote":
            try:
                dxy = bars.get("DXY", "1d")[0]
            except LookupError:
                dxy = None
        b = mtf_bias.mtf_bias(daily, dxy=dxy, inverse_to_dxy=dxy is not None)
        b["source"] = meta.get("source")
        cache[inst] = b
        with open(path, "w") as f:
            json.dump(cache, f, indent=2)
    b = dict(cache[inst])
    dr = b["dealing_range"]
    b["zone_pct_now"] = round(100 * (last - dr["low"]) / (dr["high"] - dr["low"]), 1) if dr["high"] > dr["low"] else 50.0
    return b


def context_bias(st: State, bars: Bars, today: str) -> dict:
    """Bias for the context-only instruments on the watchlist (DXY), cached with the rest, for the dashboard bias panel.
    No setups and no alerts are ever produced for them. Missing data is not an error here."""
    out = {}
    for inst in emconfig.watchlist(bars.cfg):
        if not bars.cfg["instruments"][inst].get("confluence_only"):
            continue
        try:
            daily = bars.get(inst, "1d")[0]
            out[inst] = bias_for(st, bars, inst, today, daily[-1]["close"])
        except (LookupError, ValueError):
            continue
    return out


def dxy_smt(direction: str, sweep_time: str, dxy_rows: list[dict], since: str, pair: str = "the pair") -> dict | None:
    """SMT against DXY for a USD-quote pair (EURUSD, GBPUSD move inverse to DXY).
    A short on the pair follows a buy-side sweep (new high). The dollar mirror is a new DXY LOW. If DXY did NOT make a new
    low around the sweep (+-3 min) versus its own low since `since`, the two disagree: SMT divergence, supports the trade.
    If DXY did make the new low too, there is no divergence (neutral, noted). Confluence only - never a gate."""
    t = datetime.fromisoformat(sweep_time)
    lo, hi = (t - timedelta(minutes=3)).isoformat(), (t + timedelta(minutes=3)).isoformat()
    prior = [b for b in dxy_rows if since <= b["time"] < lo]
    near = [b for b in dxy_rows if lo <= b["time"] <= hi]
    if len(prior) < 5 or not near:
        return None
    if direction == "short":
        ref, now_x, made = min(b["low"] for b in prior), min(b["low"] for b in near), "low"
        diverged = now_x >= ref
    else:
        ref, now_x, made = max(b["high"] for b in prior), max(b["high"] for b in near), "high"
        diverged = now_x <= ref
    side = "high" if direction == "short" else "low"
    if diverged:
        return {"smt": True, "text": f"DXY SMT: {pair} swept its {side} but DXY did not make the matching new {made} "
                                     f"({now_x:.3f} vs {ref:.3f}) - divergence supports the {direction}"}
    return {"smt": False, "text": f"no DXY SMT: DXY made the matching new {made} ({now_x:.3f} vs {ref:.3f})"}


# ---------------------------------------------------------------- liquidity
def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def levels_for(bars1m: list[dict], daily: list[dict] | None, bias: dict, trading_day: date, kz_start: datetime, cfg_inst: dict) -> list[dict]:
    lv = []
    lm = liquidity_levels.liquidity_map(bars1m, trading_day, daily, cfg_inst.get("eq_tol", 0.0))
    for x in lm["buy_side"] + lm["sell_side"]:
        lv.append({"name": x["name"], "level": x["level"], "side": x["side"], "formed": x["formed"]})
    for x in (bias.get("draws") or {}).get("above", []):
        lv.append({"name": x["name"], "level": x["level"], "side": "buy", "formed": None})
    for x in (bias.get("draws") or {}).get("below", []):
        lv.append({"name": x["name"], "level": x["level"], "side": "sell", "formed": None})
    day_start = datetime.combine(trading_day - timedelta(days=1), datetime.min.time().replace(hour=17), NY).astimezone(SAST)
    for i, h in detect.swing_highs(bars1m):
        if _t(bars1m[i]["time"]) >= day_start and i + 2 < len(bars1m):
            lv.append({"name": f"{'KZ' if _t(bars1m[i]['time']) >= kz_start else 'intraday'} swing high {bars1m[i]['time'][11:16]}",
                       "level": h, "side": "buy", "formed": bars1m[i + 2]["time"]})
    for i, l in detect.swing_lows(bars1m):
        if _t(bars1m[i]["time"]) >= day_start and i + 2 < len(bars1m):
            lv.append({"name": f"{'KZ' if _t(bars1m[i]['time']) >= kz_start else 'intraday'} swing low {bars1m[i]['time'][11:16]}",
                       "level": l, "side": "sell", "formed": bars1m[i + 2]["time"]})
    return lv


def _rank(name: str) -> int:
    return next((i for i, k in enumerate(LEVEL_RANK) if k in name), len(LEVEL_RANK))


def _taken_between(bars, level, side, start: str | None, end: str) -> bool:
    for b in bars:
        if (start and b["time"] <= start) or b["time"] >= end:
            continue
        if (side == "buy" and b["high"] > level) or (side == "sell" and b["low"] < level):
            return True
    return False


def pick_targets(direction: str, entry: float, stop: float, pools: list[dict], min_rr: float) -> list[dict]:
    risk = abs(stop - entry)
    side = [p for p in pools if (p["level"] < entry if direction == "short" else p["level"] > entry)]
    side.sort(key=lambda p: abs(entry - p["level"]))
    uniq = []
    for p in side:
        if not uniq or abs(p["level"] - uniq[-1]["level"]) > risk * 0.25:
            uniq.append(p)
    if not uniq:
        return []
    r = lambda p: abs(entry - p["level"]) / risk
    first = uniq[0]
    if r(first) >= min_rr:
        return [first] + uniq[1:2]
    later = next((p for p in uniq[1:] if r(p) >= min_rr), None)
    return [first, later] if later else uniq[:2]


# ---------------------------------------------------------------- scan
def scan_instrument(inst: str, bars: Bars, st: State, now: datetime, kz: dict, cfg: dict, existing: list[dict],
                    risk: dict | None = None) -> list[dict]:
    """New setups for one instrument. `risk` = riskstate.risk_state(...): when it is LOCKED, setups are still built
    (status "locked", no NEW event, never alerted); otherwise a setup that would break the daily or drawdown headroom
    is skipped and listed in risk["skipped"]."""
    rows, meta = bars.get(inst, "1m")
    ci = cfg["instruments"][inst]
    try:
        daily = bars.get(inst, "1d")[0]
    except LookupError:
        daily = None
    trading_day = bars.trading_day()
    today = now.strftime("%Y-%m-%d")
    last = rows[-1]["close"]
    try:
        bias = bias_for(st, bars, inst, today, last) if daily else {"bias": "neutral", "confidence": None, "draws": {}, "timeframes": [], "dealing_range": None}
    except ValueError:
        bias = {"bias": "neutral", "confidence": None, "draws": {}, "timeframes": [], "dealing_range": None, "reasoning": ["not enough daily history"]}
    kz_start = kz["start_sast"]
    kz_start_s = kz_start.isoformat()
    levels = levels_for(rows, daily, bias, trading_day, kz_start, ci)
    keys = {s.get("candidate_key") for s in existing}
    seen, found = set(), []
    sb = emconfig.stop_buffer(cfg, inst)
    for lv in sorted(levels, key=lambda x: _rank(x["name"])):
        direction = "short" if lv["side"] == "buy" else "long"
        start_s = max(kz_start_s, lv["formed"] or "")
        if lv["formed"] and _taken_between(rows, lv["level"], lv["side"], lv["formed"], kz_start_s):
            continue  # not resting liquidity any more at the kill-zone open
        since = next((i for i, b in enumerate(rows) if b["time"] >= start_s), None)
        if since is None:
            continue
        c = detect.find_setup(rows, direction, [lv["level"]], since)[0]
        if c["stage"] != "complete":
            continue
        m = c["mss"]
        key = f"{inst}|{direction}|{m['break_time']}"
        if key in keys or key in seen:
            continue
        seen.add(key)
        if _t(m["break_time"]) < now - timedelta(minutes=30):
            continue  # stale: the scanner was not running when it formed
        uni = models.unicorn(rows, m, direction, c["ote"])
        entry = uni["entry"] if uni else c["entry"]["price"]
        fvg = (uni or {}).get("fvg") or c["entry"].get("fvg") or (m["fvgs"][-1] if m["fvgs"] else None)
        stop_min = c["raid_extreme"] + (sb["total"] if direction == "short" else -sb["total"])
        leg_i = min(len(rows) - 1, m["break_index"] + 3)
        asof = rows[leg_i]["time"]  # targets must be untaken when the setup formed (no hindsight)
        pools = [p for p in levels if p is not lv and p["side"] != lv["side"] and (p["formed"] or "") < asof
                 and not _taken_between(rows, p["level"], p["side"], p["formed"], asof + "~")]
        tps = pick_targets(direction, entry, stop_min, pools, cfg["risk"]["min_rr"])
        if not tps:
            continue
        val = validate_setup.validate(direction, entry, c["raid_extreme"], [t["level"] for t in tps], sb["spread"], sb["wick_1m"],
                                      None, bias.get("bias"), cfg["risk"]["min_rr"], ci["decimals"])
        if val["verdict"] != "VALID":
            continue
        # real recorded risk of every open trade, including setups found earlier in THIS scan
        opens = [(s["instrument"], s["direction"], s.get("risk_pct")) for s in list(existing) + found if s.get("status") in OPEN]
        rk = position_size.risk_pct_for(inst, direction, opens)
        if not rk["allowed"]:
            continue
        locked = bool(risk and risk.get("locked"))
        if risk is not None and not locked:
            extra = sum(f["risk_pct"] for f in found)
            hr = riskstate.headroom({**risk, "open_risk_pct": risk["open_risk_pct"] + extra}, rk["risk_pct"])
            if not hr["allowed"]:
                risk.setdefault("skipped", []).append({"instrument": inst, "direction": direction, "key": key, "reasons": hr["reasons"]})
                continue
        leg_eq = (c["raid_extreme"] + m["leg_end"]) / 2
        fmid = (fvg["low"] + fvg["high"]) / 2 if fvg else None
        wk = next((t for t in bias.get("timeframes", []) if t["tf"] == "weekly"), None)
        dr = bias.get("dealing_range") or {}
        today_rows = [b for b in rows if b["time"] >= (datetime.combine(trading_day - timedelta(days=1), datetime.min.time().replace(hour=17), NY).astimezone(SAST)).isoformat()]
        took = None
        if dr and today_rows:
            took = "high" if max(b["high"] for b in today_rows) > dr["high"] else "low" if min(b["low"] for b in today_rows) < dr["low"] else None
        lon = killzone.killzones_for(trading_day)[1]
        lon_rows = [b for b in rows if lon["start_sast"].isoformat() <= b["time"] < lon["end_sast"].isoformat()]
        ctx = {"instrument": inst, "direction": direction, "entry_time": m["break_time"], "entry_tf": "1m", "sweep_tf": "1m",
               "sweep_label": lv["name"], "mss_valid": True, "in_ote": c["ote"]["low"] <= entry <= c["ote"]["high"],
               "unicorn": bool(uni), "fvg_side_of_eq": None if fmid is None else ("premium" if fmid >= leg_eq else "discount"),
               "zone_pct": bias.get("zone_pct_now"), "daily_took_20d": took,
               "weekly_bias": None if not wk else {1: "bull", -1: "bear", 0: "neutral"}[wk["score"]],
               "daily_bias": bias.get("bias"), "bias_confidence": bias.get("confidence"),
               "tp1_dist": entry - tps[0]["level"], "pip": ci.get("pip"),
               "london_direction": None if kz["name"] != "NY AM" or len(lon_rows) < 2 else ("up" if lon_rows[-1]["close"] > lon_rows[0]["open"] else "down"),
               "target_is_old_daily": any(k in tps[0]["name"] for k in ("PDH", "PDL", "old", "PW", "PM")),
               "weekly_expansion": None if not wk else ("took prior low" in wk["context"] if direction == "short" else "took prior high" in wk["context"]),
               "liquidity_unswept": True}
        smt = None
        if ci.get("dxy_relation") == "inverse":
            try:
                day_start = datetime.combine(trading_day - timedelta(days=1), datetime.min.time().replace(hour=17), NY).astimezone(SAST)
                smt = dxy_smt(direction, c["sweep"]["time"], bars.get("DXY", "1m")[0], day_start.isoformat(), inst)
            except LookupError:
                smt = None
        charters = models.charter_models(ctx)
        names = models.name_models(uni, charters)
        sid = st.next_id(inst) if not found else f"{PREFIX[inst]}{int(st.next_id(inst)[1:]) + len(found)}"
        d = ci["decimals"]
        targets = [{**t, "price": t["price"], "label": tps[i]["name"]} for i, t in enumerate(val["targets"])]
        s = {"id": sid, "date": today, "instrument": inst, "symbol": ci["tv"], "decimals": d, "timeframe": "1m",
             "direction": direction, "killzone": f"{kz['name']} ({kz['ny']} NY = {kz['start_sast']:%H:%M}-{kz['end_sast']:%H:%M} SAST)",
             "killzone_end": kz["end_sast"].isoformat(), "killzone_end_sast": f"{kz['end_sast']:%H:%M}",
             "model": names["model"], "charter_model": names["charter_model"], "charter_checks": charters,
             "charter_stage": models.charter_stage("pending"),
             "daily_bias": bias.get("bias"), "bias_detail": {k: bias.get(k) for k in ("label", "confidence", "confidence_label", "draw", "invalidation")},
             "bias_label": val["bias_label"],
             "sweep": {"description": f"{c['sweep']['time'][11:16]} ran {lv['name']} {lv['level']:.{d}f} ({lv['side']}-side)",
                       "level": lv["level"], "extreme": c["raid_extreme"], "extreme_time": m["raid_extreme_time"][11:16],
                       "extreme_time_iso": m["raid_extreme_time"]},
             "mss": {"level": m["swing_level"], "description": f"{m['break_time'][11:16]} closed through the {m['swing_time'][11:16]} swing {m['swing_level']:.{d}f}; leg to {m['leg_end']:.{d}f}",
                     "displacement": f"{len(m['displacement_bars'])} displacement candle(s) ({', '.join(t[11:16] for t in m['displacement_bars'])})"},
             "fvg": {"low": fvg["low"], "high": fvg["high"]} if fvg else None,
             "ote": {"low": c["ote"]["low"], "high": c["ote"]["high"]},
             "unicorn": uni, "entry": round(entry, d), "spread": sb["spread"], "wick": sb["wick_1m"],
             "stop": val["stop"], "risk_pts": val["risk_pts"], "targets": targets,
             "risk_pct": rk["risk_pct"], "risk_mode": rk["mode"],
             "size_formula": f"equity x {rk['risk_pct'] / 100:g} / {val['risk_pts']:g} pts",
             "confluence": [f"MTF bias {bias.get('label', bias.get('bias'))} (confidence {bias.get('confidence')})"]
                           + [r for r in bias.get("reasoning", []) if "confirms" in r]
                           + ([f"draw on liquidity {bias['draw']['name']} {bias['draw']['level']}"] if bias.get("draw") else [])
                           + ([smt["text"]] if smt and smt["smt"] else []),
             "dxy_smt": smt,
             "caveats": ([rk["note"]] if rk.get("note") else []) + (["stop buffer is a DEFAULT PLACEHOLDER pending your Capital.com spread"] if sb["is_default"] else [])
                        + [r for r in bias.get("reasoning", []) if r.startswith("EXTENDED") or "DISAGREES" in r],
             "data_source": meta.get("source"), "fallback": bool(meta.get("fallback")),
             "validation": {"verdict": val["verdict"], "errors": val["errors"], "notes": val["notes"]},
             "status": "pending", "candidate_key": key, "alert_time": now.isoformat(), "last_checked": m["break_time"],
             "events": [{"event": "NEW", "time": now.isoformat(), "price": None, "r": None, "note": ""}]}
        if locked:
            s.update(status="locked", charter_stage=models.charter_stage("locked"), events=[], lock=risk["lock"])
            s["caveats"] = [f"LOCKED: {risk['lock']['reason']} - recorded, not alerted"] + s["caveats"]
        found.append(s)
    return found


# ---------------------------------------------------------------- outbox
def _chart(s: dict, rows: list[dict], event: str, root: str) -> str | None:
    try:
        import render_chart
        out = os.path.join(root, "charts", f"{s['instrument'].lower()}_{s['id']}_{s['date'][5:7]}{s['date'][8:10]}_{event.lower()}.png")
        return render_chart.render(s, rows, out, event)
    except Exception:  # noqa: BLE001 - chart is best effort; text still goes
        return None


MAX_AGE = timedelta(hours=6)  # measured from when the cycle DETECTED the event (detected_at), not the bar time


def outbox(st: State, bars: Bars, dry_run: bool, env, charts: bool = True) -> dict:
    """Undelivered events (no log row) in order. A failed/blocked event stops later events of the same setup this cycle,
    so Hilda never gets FILLED before NEW. Events detected more than MAX_AGE ago (still undelivered) are reported as
    expired, not sent. Age runs from `detected_at`, so an event found late (e.g. a CANCELLED stamped at the 17:00 kill-zone
    end but first seen on the next morning's run) is still delivered once."""
    report = {"sent": [], "failed": [], "blocked": [], "dry_run": [], "expired": []}
    logged = {(r["setup_id"], r["event"]) for r in alert_log.read(st.log)}
    for s in st.setups():
        for ev in s.get("events", []):
            e = ev["event"]
            if (s["id"], e) in logged:
                continue
            seen = ev.get("detected_at") or ev.get("time")
            if seen and bars.now - datetime.fromisoformat(seen) > MAX_AGE:
                report["expired"].append({"id": s["id"], "event": e, "time": ev["time"]}); continue
            s_view = {**s, "charter_stage_override": None}
            txt = render_alert.render(s_view, e, None if e == "NEW" else ev["time"][11:16], ev.get("price"), ev.get("note", ""))
            errs = check_alert.check(txt, e, s)
            if errs:
                report["blocked"].append({"id": s["id"], "event": e, "errors": errs}); break
            try:
                rows = bars.get(s["instrument"], "1m")[0]
            except LookupError:
                rows = []
            chart = _chart(s, rows, e, st.root) if charts and rows else None
            os.makedirs(os.path.join(st.root, "alerts"), exist_ok=True)
            with open(os.path.join(st.root, "alerts", f"{s['date']}_{s['id']}_{e}.md"), "w") as f:
                f.write(txt + "\n")
            row = {"date": s["date"], "session": s["killzone"].split(" (")[0], "instrument": s["instrument"], "direction": s["direction"],
                   "label": s.get("bias_label", ""), "model": s.get("model", ""), "alert_time_sast": (ev["time"] or "")[11:16],
                   "swept": s["sweep"].get("description", ""), "mss": s["mss"].get("description", ""),
                   "zone": f"entry {s['entry']}", "stop": s["stop"], "target": " / ".join(str(t["price"]) for t in s["targets"]),
                   "r": "" if ev.get("r") is None else ev["r"]}
            res = notify.deliver_and_log(s["id"], e, txt, chart, row, st.log, dry_run, env)
            key = {"id": s["id"], "event": e, "chart": chart}
            if dry_run:
                report["dry_run"].append({**key, "text": txt})
            elif res.get("sent"):
                report["sent"].append({**key, "channels": res["sent"]})
            else:
                report["failed"].append({**key, "errors": res.get("failed")})
                break
    return report


def lock_outbox(st: State, rs: dict, dry_run: bool, env, report: dict) -> None:
    """One LOCKED alert per lock (key: lock id + LOCKED in the alert log), sent after the events that caused it."""
    if not rs.get("locked"):
        return
    lk = rs["lock"]
    txt = render_alert.render_lock(rs)
    errs = check_alert.check(txt, "LOCKED")
    if errs:
        report["blocked"].append({"id": lk["id"], "event": "LOCKED", "errors": errs}); return
    row = {"date": rs["now"][:10], "session": "", "instrument": "ALL", "direction": "", "label": "", "model": "",
           "alert_time_sast": rs["now"][11:16], "swept": "", "mss": lk["reason"], "zone": "", "stop": "", "target": "", "r": ""}
    res = notify.deliver_and_log(lk["id"], "LOCKED", txt, None, row, st.log, dry_run, env, subject=f"[ICT alert] RISK LOCKED ({lk['kind']})")
    key = {"id": lk["id"], "event": "LOCKED", "chart": None}
    if res.get("skipped"):
        return
    if dry_run:
        report["dry_run"].append({**key, "text": txt})
    elif res.get("sent"):
        report["sent"].append({**key, "channels": res["sent"]})
    else:
        report["failed"].append({**key, "errors": res.get("failed")})


def save_bars(root: str, bars: Bars, keep: int = 600) -> None:
    """Write the closed 1m bars of this cycle to state/bars/<INST>_1m.csv (+ .meta.json) so scripts/dashboard.py can draw
    the setup charts without fetching anything. Best effort."""
    d = os.path.join(root, "state", "bars")
    for (inst, iv), (rows, meta) in list(bars.cache.items()):
        if iv != "1m" or not rows:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{inst}_1m.csv")
            with open(path + ".tmp", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close"])
                w.writeheader()
                for r in rows[-keep:]:
                    w.writerow({k: r[k] for k in ("time", "open", "high", "low", "close")})
            os.replace(path + ".tmp", path)
            with open(path + ".meta.json", "w") as f:
                json.dump({**meta, "saved_at": bars.now.isoformat()}, f)
        except OSError:
            pass


# ---------------------------------------------------------------- cycle
def run_cycle(now: datetime, root: str = ROOT, bars_dir: str | None = None, dry_run: bool = False, env=None,
              instruments: list[str] | None = None, charts: bool = True) -> dict:
    env = os.environ if env is None else env
    cfg = emconfig.load_config()
    st, bars = State(root), Bars(bars_dir, now, cfg)
    report = {"now_sast": now.strftime("%Y-%m-%d %H:%M"), "events": [], "new": [], "locked": [], "errors": []}
    # 1. lifecycle
    for s in st.setups():
        if s.get("status") not in OPEN:
            continue
        try:
            rows = bars.get(s["instrument"], "1m")[0]
        except LookupError as e:
            report["errors"].append(str(e)); continue
        status, evs = lifecycle.evaluate(s, rows, s.get("killzone_end"), now)
        for e in evs:
            e.setdefault("detected_at", now.isoformat())
        s["last_checked"] = rows[-1]["time"]
        if evs:
            s["status"] = status
            s["charter_stage"] = models.charter_stage(status)
            s.setdefault("events", []).extend(evs)
            report["events"] += [f"{s['id']} {e['event']}" for e in evs]
        st.save(s)
    # risk state after the lifecycle replay (a stop just booked can lock the account)
    rs = riskstate.risk_state(st.setups(), now, cfg)
    # 2. scan
    kz = killzone.in_watched_killzone(now, tuple(cfg.get("watch_killzones", killzone.DEFAULT_WATCHED)))
    report["killzone"] = kz["name"] if kz else None
    if kz:
        for inst in instruments or emconfig.tradeable(cfg):   # switched-on, not context-only: US100 US500 EURUSD GBPUSD
            try:
                new = scan_instrument(inst, bars, st, now, kz, cfg, st.setups(), rs)
            except LookupError as e:
                report["errors"].append(str(e)); continue
            for s in new:
                if s["status"] == "locked":
                    st.save(s); report["locked"].append(s["id"]); continue
                rows = bars.get(inst, "1m")[0]
                status, evs = lifecycle.evaluate(s, rows, s.get("killzone_end"), now)
                for e in evs:
                    e.setdefault("detected_at", now.isoformat())
                if status not in ("pending", "filled"):
                    continue  # already past TP1, void, invalid or closed before it could be alerted: not actionable, no stale burst
                s["status"], s["last_checked"] = status, rows[-1]["time"]
                s["charter_stage"] = models.charter_stage(status) if status != "pending" else s["charter_stage"]
                s["events"] += evs
                st.save(s)
                report["new"].append(s["id"])
        if not instruments:
            context_bias(st, bars, now.strftime("%Y-%m-%d"))   # DXY bias for the dashboard; never scanned or alerted
    # 3. outbox
    report["outbox"] = outbox(st, bars, dry_run, env, charts)
    lock_outbox(st, rs, dry_run, env, report["outbox"])
    report["risk"] = {k: rs[k] for k in ("trading_day", "daily_pct", "drawdown_pct", "open_risk_pct", "locked", "lock")}
    report["risk"]["skipped"] = rs.get("skipped", [])
    # 4. findings + the closed 1m bars this cycle used, for the local dashboard (state/bars/, git-ignored)
    save_bars(st.root, bars)
    srcs = sorted({m.get("source", "") for (_, iv), (_, m) in bars.cache.items() if iv == "1m"})
    text = write_setups_md.build(sorted(st.setups(), key=lambda s: s.get("alert_time", "")), [], now, "; ".join(srcs))
    os.makedirs(os.path.join(root, "findings"), exist_ok=True)
    with open(os.path.join(root, "findings", "setups.md"), "w") as f:
        f.write(text)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now", help="ISO time (default: now, SAST)"); p.add_argument("--bars-dir")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--instrument", action="append")
    p.add_argument("--no-charts", action="store_true"); p.add_argument("--env-file", default=os.path.join(ROOT, ".env"))
    p.add_argument("--quiet", action="store_true", help="print nothing when nothing changed")
    a = p.parse_args(argv)
    notify.load_env_file(a.env_file)
    now = datetime.fromisoformat(a.now).astimezone(SAST) if a.now else datetime.now(SAST)
    rep = run_cycle(now, ROOT, a.bars_dir, a.dry_run, None, a.instrument, not a.no_charts)
    ob = rep["outbox"]
    changed = rep["events"] or rep["new"] or ob["sent"] or ob["failed"] or ob["blocked"] or ob["dry_run"]
    if changed or not a.quiet:
        print(json.dumps(rep, indent=2, default=str))
    if len(rep["errors"]) and not rep["new"] and all("NO DATA" in e for e in rep["errors"]) and rep.get("killzone"):
        return 3
    return 1 if ob["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
