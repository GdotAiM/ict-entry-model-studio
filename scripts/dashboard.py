#!/usr/bin/env python3
"""Local web dashboard (static HTML, stdlib only). Reads the real state - nothing here fetches prices or sends anything.

Pages written to dashboard/ (git-ignored):
  index.html          kill-zone timeline, watchlist + daily bias, live setups with status, alert feed, risk meters
  setup-<ID>.html     one setup: annotated 1m chart, numbered checklist (why it qualified), event timeline
  lifecycle.html      the state machine with live counts per state, and recent transitions
  recap.html          weekly recap from state/alerts_log.csv with the UNPROVEN badge (see proof_status)

Sources: state/setups/*.json, state/alerts_log.csv, state/bias_<date>.json, state/bars/<INST>_1m.csv (written by
watch.py each cycle), config/instruments.json (+ .local), config/preregistration.json (optional, see proof_status).
Risk meters come from lib/riskstate.py (the same code that LOCKS the watcher). Colours come from lib/palette.py.

    python3 scripts/dashboard.py                    # write dashboard/ from the live state, now = current time
    python3 scripts/dashboard.py --serve [--port 8765]   # tiny local server on 127.0.0.1, rebuilds on every page load
    python3 scripts/dashboard.py --example [--out /tmp/emdash]   # EXAMPLE DATA: fabricated week + offline replay
    python3 scripts/dashboard.py --now 2026-01-15T09:40:00+02:00 --root DIR --out DIR
"""
from __future__ import annotations
import argparse, csv, glob, html, json, math, os, shutil, sys, tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in ("lib", "scripts", *[os.path.join(".claude", "skills", s, "scripts") for s in ("killzones", "risk-rules", "setup-detection")]):
    sys.path.insert(0, os.path.join(ROOT, p))
import emconfig, palette, riskstate, killzone, position_size, recap  # noqa: E402

SAST, NY = ZoneInfo("Africa/Johannesburg"), ZoneInfo("America/New_York")
OPEN = {"pending", "filled", "tp1"}
E = html.escape
T, LV = palette.THEME, palette.LEVEL


# =============================================================== data
def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_setups(root: str) -> list[dict]:
    out = [_read_json(f) for f in sorted(glob.glob(os.path.join(root, "state", "setups", "*.json")))]
    return sorted([s for s in out if s], key=lambda s: s.get("alert_time", ""))


def load_log(root: str) -> list[dict]:
    path = os.path.join(root, "state", "alerts_log.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_bars(root: str, inst: str, bars_dir: str | None = None) -> tuple[list[dict], dict]:
    for d in [x for x in (bars_dir, os.path.join(root, "state", "bars")) if x]:
        p = os.path.join(d, f"{inst}_1m.csv")
        if os.path.exists(p):
            with open(p, newline="") as f:
                rows = [{"time": r["time"], **{k: float(r[k]) for k in ("open", "high", "low", "close")}} for r in csv.DictReader(f)]
            return rows, _read_json(p + ".meta.json", {"source": f"local file {os.path.basename(p)}"})
    return [], {}


def load_bias(root: str, now: datetime) -> tuple[dict, str | None]:
    """Latest daily bias cache on or before today (SAST date). Returns (per-instrument bias, date of the file)."""
    files = sorted(glob.glob(os.path.join(root, "state", "bias_*.json")))
    today = now.astimezone(SAST).strftime("%Y-%m-%d")
    files = [f for f in files if os.path.basename(f)[5:15] <= today]
    if not files:
        return {}, None
    return _read_json(files[-1], {}), os.path.basename(files[-1])[5:15]


def groups_usage(setups: list[dict]) -> list[dict]:
    g = defaultdict(float)
    n = defaultdict(int)
    for s in setups:
        if s.get("status") in OPEN:
            k = position_size.correlation_group(s["instrument"], s["direction"])
            g[k] += float(s.get("risk_pct") or 0); n[k] += 1
    return sorted([{"group": k, "used": round(v, 4), "n": n[k]} for k, v in g.items()], key=lambda x: -x["used"])


def is_example(setups, meta_sources) -> bool:
    return any(s.get("example") or "EXAMPLE" in str(s.get("data_source", "")).upper() for s in setups) or \
        any("EXAMPLE" in str(m).upper() for m in meta_sources)


def collect(root: str, now: datetime, cfg: dict | None = None, bars_dir: str | None = None) -> dict:
    cfg = cfg or emconfig.load_config()
    setups = load_setups(root)
    log = load_log(root)
    rs = riskstate.risk_state(setups, now, cfg)
    bias, bias_date = load_bias(root, now)
    td = riskstate.trading_day(now)
    kzs = killzone.killzones_for(td) if td.weekday() < 5 else []
    watched = tuple(cfg.get("watch_killzones", killzone.DEFAULT_WATCHED))
    bars = {}
    for inst in {s["instrument"] for s in setups} | set(cfg["instruments"]):
        rows, meta = load_bars(root, inst, bars_dir)
        if rows:
            bars[inst] = (rows, meta)
    srcs = [m.get("source") for _, m in bars.values()] + [s.get("data_source") for s in setups]
    return {"now": now.astimezone(SAST), "cfg": cfg, "setups": setups, "log": log, "risk": rs, "bias": bias, "bias_date": bias_date,
            "trading_day": td, "killzones": kzs, "watched": watched, "bars": bars, "groups": groups_usage(setups),
            "example": is_example(setups, srcs), "sources": sorted({x for x in srcs if x})}


# =============================================================== proof status (UNPROVEN badge)
def proof_status(results: list[dict], prereg: dict | None) -> dict:
    """UNPROVEN unless a PRE-REGISTERED threshold is met on trades AFTER the registration date.

    config/preregistration.json (optional; commit it BEFORE the test window - git history is the audit trail):
      {"id": "EM-1", "registered_at": "2026-12-01", "hypothesis": "...", "min_trades": 30,
       "rule": "mean_r_ci95_low_above", "threshold": 0.0}
    Only closed, taken trades dated after registered_at count (out of sample). Verdicts:
      UNPROVEN                      no pre-registration, or fewer than min_trades out-of-sample trades
      MEETS PRE-REGISTERED TEST     n >= min_trades and the lower 95% bound of mean R > threshold
      FAILS PRE-REGISTERED TEST     n >= min_trades and it does not
    In-sample trades (before registration) never change the badge, however good they look."""
    taken = [r for r in results if r.get("taken") and r.get("r") is not None]
    if not prereg or not prereg.get("registered_at"):
        return {"badge": "UNPROVEN", "key": "unproven", "n_oos": 0, "n_all": len(taken), "min_trades": None,
                "reason": f"No pre-registered test yet. n = {len(taken)} closed trade(s) logged. Treat as a process log, "
                          "not evidence of edge."}
    need = int(prereg.get("min_trades", 30))
    oos = [r for r in taken if r["date"] > str(prereg["registered_at"])[:10]]
    n = len(oos)
    if n < need:
        return {"badge": "UNPROVEN", "key": "unproven", "n_oos": n, "n_all": len(taken), "min_trades": need,
                "reason": f"Pre-registered test {prereg.get('id', '')} has {n} of {need} out-of-sample trades."}
    rs_ = [r["r"] for r in oos]
    mean = sum(rs_) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in rs_) / (n - 1)) if n > 1 else 0.0
    low = mean - 1.96 * sd / math.sqrt(n)
    ok = low > float(prereg.get("threshold", 0.0))
    return {"badge": "MEETS PRE-REGISTERED TEST" if ok else "FAILS PRE-REGISTERED TEST", "key": "meets" if ok else "fails",
            "n_oos": n, "n_all": len(taken), "min_trades": need, "mean": round(mean, 3), "ci_low": round(low, 3),
            "reason": f"{prereg.get('id', '')}: n = {n} out-of-sample, mean {mean:+.2f}R, 95% lower bound {low:+.2f}R "
                      f"vs threshold {float(prereg.get('threshold', 0.0)):+.2f}R."}


# =============================================================== html helpers
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:13px/1.45 'Geist','Inter',system-ui,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased;font-feature-settings:'tnum' 1}
a{color:inherit;text-decoration:none} a:hover{text-decoration:underline}
.mono{font-family:'Geist Mono','JetBrains Mono',ui-monospace,Menlo,monospace;font-feature-settings:'tnum' 1,'zero' 1}
.mut{color:var(--mut)} .tx2{color:var(--tx2)}
.wrap{padding:18px 20px 28px;max-width:1500px;margin:0 auto}
.top{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--bg2)}
.logo{width:26px;height:26px;border-radius:7px;background:linear-gradient(135deg,#4f9dff,#a78bfa)}
.top{white-space:nowrap} .top h1{font-size:15px;font-weight:650} .top .clock{font-size:22px;font-weight:650;margin-left:18px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border:1px solid var(--line2);border-radius:7px;font-size:11.5px;color:var(--tx2);background:var(--panel);white-space:nowrap}
.chip.warn{color:#f5b041;border-color:#5a4318;background:#3a2c12}
.ex{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#f5b041;border:1px dashed #5a4318;padding:3px 8px;border-radius:6px}
.nav{display:flex;gap:4px;margin-left:auto} .nav a{padding:5px 10px;border-radius:7px;color:var(--tx2);font-size:12.5px} .nav a.on{background:var(--panel2);color:var(--tx)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px}
.card h2{font-size:13px;font-weight:650;padding:12px 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
.card h2 .r{margin-left:auto;font-weight:400;color:var(--mut);font-size:11.5px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:650;letter-spacing:.02em;white-space:nowrap;border:1px solid transparent}
.pill i{width:7px;height:7px;border-radius:50%;display:inline-block;background:currentColor}
.tag{display:inline-block;padding:1px 6px;border-radius:5px;font-size:10.5px;font-weight:650;letter-spacing:.04em;text-transform:uppercase}
table{width:100%;border-collapse:collapse} th{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);font-weight:500;text-align:left;padding:8px 12px;border-bottom:1px solid var(--line)}
td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:middle} tr:last-child td{border-bottom:0}
.meter{height:7px;border-radius:4px;background:var(--line2);position:relative;overflow:hidden;margin:7px 0 5px}
.meter b{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.grid3{display:grid;grid-template-columns:270px 1fr 300px;gap:14px;margin-top:14px}
.kv{display:flex;justify-content:space-between;gap:10px}
.stat{padding:12px 14px} .stat .l{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut)} .stat .v{font-size:20px;font-weight:650;margin-top:3px}
.foot{margin-top:16px;color:var(--mut);font-size:11.5px}
.ok{color:#22c55e} .bad{color:#f25555} .warnc{color:#f5b041}
"""


def page(title: str, active: str, body: str, ctx: dict, refresh: int | None = None) -> str:
    now = ctx["now"]
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{n}</a>' for k, h, n in
                  (("dash", "index.html", "Dashboard"), ("life", "lifecycle.html", "Lifecycle"), ("recap", "recap.html", "Weekly recap")))
    src = ctx["sources"][-1] if ctx["sources"] else "no bar data yet"
    ex = '<span class="ex" title="fabricated, not market data">EXAMPLE DATA</span>' if ctx["example"] else ""
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{meta}<title>{E(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{palette.css()}{CSS}</style></head><body>
<div class="top"><div class="logo"></div><h1>ICT Entry Model Studio</h1>
<span class="clock mono">{now:%H:%M}</span><span class="mut mono">SAST · {now.astimezone(NY):%H:%M} NY · {now:%a %d %b}</span>
<nav class="nav">{nav}</nav><span class="chip">{E(src[:44])}</span><span class="chip">Alerts only · no orders</span>{ex}</div>
<div class="wrap">{body}<div class="foot">Analysis only, not financial advice. Nothing here places or manages orders. Generated {now:%Y-%m-%d %H:%M} SAST from the local state.</div></div></body></html>"""


def pill(status_or_event: str, text: str | None = None) -> str:
    k = palette.status_key(status_or_event)
    return f'<span class="pill p-{k}"><i></i>{E(text or palette.label(status_or_event))}</span>'


def dirtag(d: str) -> str:
    return f'<span class="tag {d}">{"▼ SHORT" if d == "short" else "▲ LONG"}</span>'


def fmt(x, d=2):
    return "–" if x is None else f"{x:,.{d}f}"


def r_of(s: dict) -> float | None:
    """Realised R of a setup so far (recap convention)."""
    ps = 0.5
    rows = riskstate.realised(s, ps)
    risk = float(s.get("risk_pct") or 0)
    return round(sum(p for _, p, _ in rows) / risk, 2) if rows and risk else None


def rcell(r: float | None) -> str:
    if r is None:
        return ""
    cls = "ok" if r > 0 else "bad" if r < 0 else "mut"
    return f'<b class="{cls}">{r:+.2f}R</b><br>'


def meter(label: str, used: float, cap: float, sub: str, color: str, alarm: bool = True) -> str:
    pct = 0 if cap <= 0 else min(100.0, 100 * used / cap)
    col = color if not alarm else palette.STATUS["stop"]["fg"] if pct >= 100 else palette.STATUS["filled"]["fg"] if pct >= 75 else color
    return (f'<div style="padding:12px 14px;border-bottom:1px solid var(--line)"><div class="kv"><b>{E(label)}</b>'
            f'<span class="mono"><b style="color:{col}">{used:.2f}%</b> <span class="mut">/ {cap:.2f}%</span></span></div>'
            f'<div class="meter"><b style="width:{pct:.1f}%;background:{col}"></b></div><div class="mut" style="font-size:11.5px">{sub}</div></div>')


# =============================================================== dashboard page
def kz_timeline(ctx: dict) -> str:
    now = ctx["now"]
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    W = 100.0

    def x(t):
        return max(0.0, min(W, (t - day0).total_seconds() / 864))

    short = {"Asian": "Asia", "Silver Bullet": "SB", "London Close": "LC", "NY PM": "NY PM"}
    blocks = []
    kzs = [k for k in ctx["killzones"] if k["end_sast"] > day0 and k["start_sast"] < day0 + timedelta(days=1)]
    starts = defaultdict(list)
    for k in kzs:
        if k["name"] not in ctx["watched"]:
            starts[k["start_sast"]].append(short.get(k["name"], k["name"]))
    done = set()
    for k in kzs:
        x1, x2 = x(k["start_sast"]), x(k["end_sast"])
        if k["name"] in ctx["watched"]:
            blocks.append(f'<div style="position:absolute;left:{x1:.2f}%;width:{x2 - x1:.2f}%;top:40px;height:22px;border-radius:5px;'
                          f'background:{palette.STATUS["pending"]["bg"]};border:1px solid {palette.STATUS["pending"]["fg"]}"></div>'
                          f'<div style="position:absolute;left:{x1:.2f}%;top:22px;font-size:11.5px;font-weight:650;white-space:nowrap">{E(k["name"])} '
                          f'<span class="mono mut" style="font-weight:400">{k["start_sast"]:%H:%M}-{k["end_sast"]:%H:%M}</span></div>')
        else:
            blocks.append(f'<div style="position:absolute;left:{x1:.2f}%;width:{x2 - x1:.2f}%;top:47px;height:8px;border-radius:4px;'
                          f'background:{T["panel2"]};border:1px solid {T["line2"]}"></div>')
            if k["start_sast"] not in done:
                done.add(k["start_sast"])
                blocks.append(f'<div class="mut" style="position:absolute;left:{x1:.2f}%;top:64px;font-size:10.5px;white-space:nowrap">'
                              f'{E(" / ".join(starts[k["start_sast"]]))} (not scanned)</div>')
    ticks = "".join(f'<div class="mono mut" style="position:absolute;left:{h / 24 * 100:.2f}%;top:84px;font-size:10px;transform:translateX(-50%);text-align:center">'
                    f'{h:02d}:00<br><span style="opacity:.7">{(day0 + timedelta(hours=h)).astimezone(NY):%H:%M} NY</span></div>' for h in range(0, 25, 3))
    nowx = x(now)
    blocks.append(f'<div style="position:absolute;left:{nowx:.2f}%;top:14px;height:66px;width:2px;background:#e8ecf3"></div>'
                  f'<div class="mono" style="position:absolute;left:{nowx:.2f}%;top:0;transform:translateX(-50%);background:#e8ecf3;color:#090c12;font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:4px">NOW {now:%H:%M}</div>')
    cur = killzone.in_watched_killzone(now, ctx["watched"])
    if cur:
        left = cur["end_sast"] - now
        h, m = divmod(int(left.total_seconds() // 60), 60)
        status = f'<b>{E(cur["name"])}</b> ends in <b class="warnc">{h}h {m:02d}m</b> · unfilled setups auto-cancel at {cur["end_sast"]:%H:%M} SAST'
    else:
        nxt = next((k for k in kzs if k["name"] in ctx["watched"] and k["start_sast"] > now), None)
        status = (f'Next scan window <b>{E(nxt["name"])}</b> at {nxt["start_sast"]:%H:%M} SAST' if nxt else "No watched kill zone left today")
    return (f'<div class="card"><h2>Kill zones today <span class="mut" style="font-weight:400">· New York-defined, shown in SAST · only '
            f'{" & ".join(ctx["watched"])} are scanned</span><span class="r" style="color:var(--tx2)">{status}</span></h2>'
            f'<div style="position:relative;height:118px;margin:12px 30px 8px">{"".join(blocks)}{ticks}</div></div>')


def watchlist(ctx: dict) -> str:
    rows = []
    for inst, spec in ctx["cfg"]["instruments"].items():
        b = ctx["bias"].get(inst)
        bars = ctx["bars"].get(inst)
        last = bars[0][-1]["close"] if bars else None
        d = spec.get("decimals", 2)
        if b:
            k = (b.get("bias") or "neutral").lower()
            col = "tp" if k.startswith("bull") else "stop" if k.startswith("bear") else "be"
            dr = b.get("dealing_range") or {}
            pos = dr.get("position_pct")
            draw = b.get("draw") or {}
            line = (f'<span class="pill p-{col}" style="font-size:10.5px">{E((b.get("label") or k).upper())}</span> '
                    f'<span class="mut">conf {b.get("confidence")}</span>'
                    + (f'<div class="meter" style="height:5px"><b style="width:{pos}%;background:{T["tx2"]}"></b></div>'
                       f'<div class="kv mut" style="font-size:10.5px"><span>Discount</span><span>{pos}% of 20d range</span><span>Premium</span></div>' if pos is not None else "")
                    + (f'<div class="mut" style="font-size:11px">Draw: {E(str(draw.get("name", "")))} {fmt(draw.get("level"), d)}</div>' if draw else ""))
        else:
            line = '<span class="mut" style="font-size:11.5px">no bias read yet (no daily data this run)</span>'
        tag = ' <span class="mut" style="font-size:10.5px">confluence only</span>' if spec.get("confluence_only") else ""
        rows.append(f'<div style="padding:11px 14px;border-bottom:1px solid var(--line)"><div class="kv"><b>{E(inst)}</b>'
                    f'<span class="mono">{fmt(last, d) if last is not None else "<span class=mut>–</span>"}</span></div>'
                    f'<div class="mut mono" style="font-size:10.5px;margin-bottom:5px">{E(spec.get("tv", ""))}{tag}</div>{line}</div>')
    when = f"bias file {ctx['bias_date']}" if ctx["bias_date"] else "no bias file"
    return f'<div class="card"><h2>Watchlist &amp; bias<span class="r">{when}</span></h2>{"".join(rows)}</div>'


def setups_table(ctx: dict) -> str:
    td = ctx["trading_day"]
    show = [s for s in ctx["setups"] if s.get("status") in OPEN or riskstate.trading_day(datetime.fromisoformat(s["alert_time"])) == td]
    rows = []
    for s in reversed(show):
        d = s.get("decimals", 2)
        tps = s.get("targets") or []
        last = (s.get("events") or [{}])[-1]
        lt = datetime.fromisoformat(last.get("time") or s["alert_time"]).astimezone(SAST)
        r = r_of(s)
        st = s.get("status", "pending")
        text = palette.label(st) + (" · runner open" if st == "tp1" else "")
        rows.append(f'<tr><td class="mono"><a href="setup-{E(s["id"])}.html"><b>{E(s["id"])}</b></a></td>'
                    f'<td><b>{E(s["instrument"])}</b><br>{dirtag(s["direction"])}</td>'
                    f'<td>{E((s.get("model") or "").split(" (")[0])}<br><span class="mut">{E(s.get("killzone", "").split(" (")[0])}</span>'
                    + (" " + CT_TAG if s.get("bias_label") == "COUNTER-TREND" else "")
                    + f'</td><td class="mono">{fmt(s.get("entry"), d)}</td><td class="mono bad">{fmt(s.get("stop"), d)}</td>'
                    f'<td class="mono ok">{"<br>".join(fmt(t["price"], d) for t in tps[:2])}</td>'
                    f'<td class="mono">{rcell(r)}<span class="mut">{s.get("risk_pct")}%</span></td>'
                    f'<td>{pill(st, text)}</td><td class="mono">{lt:%H:%M}<br><span class="mut">{lt.astimezone(NY):%H:%M} NY</span></td></tr>')
    if not rows:
        rows = ['<tr><td colspan="9" class="mut" style="padding:22px">No setups this trading day yet.</td></tr>']
    n_open = sum(1 for s in show if s.get("status") in OPEN)
    return (f'<div class="card"><h2>Setups<span class="r">trading day {td:%a %d %b} + still open · {n_open} open · {len(show) - n_open} closed</span></h2>'
            f'<table><tr><th>ID</th><th>Instrument</th><th>Model · kill zone</th><th>Entry</th><th>Stop</th><th>TP1 / TP2</th><th>R · risk</th><th>Status</th><th>Last SAST</th></tr>{"".join(rows)}</table></div>')


def alert_feed(ctx: dict, n: int = 9) -> str:
    rows = []
    for r in list(reversed(ctx["log"]))[:n]:
        ev = r["event"]
        rv = f' · {float(r["r"]):+.2f}R' if r.get("r") not in (None, "") and ev not in ("NEW", "FILLED") else ""
        what = {"NEW": f'NEW {r["instrument"]} {r["direction"]} · {r.get("model", "").split(" (")[0]}',
                "LOCKED": f'RISK LOCKED · {r.get("mss", "")}'}.get(ev, f'{ev} {r["instrument"]}{rv}')
        rows.append(f'<tr><td class="mono" style="width:120px;white-space:nowrap">{E(r["date"][5:])} {E(r.get("alert_time_sast", ""))}</td>'
                    f'<td class="mono" style="width:60px;white-space:nowrap"><b>{"RISK" if ev == "LOCKED" else E(r["setup_id"][:8])}</b></td><td>{pill(ev, ev)}</td><td>{E(what)}</td></tr>')
    if not rows:
        rows = ['<tr><td class="mut" style="padding:18px">No alert delivered yet.</td></tr>']
    return f'<div class="card" style="margin-top:14px"><h2>Alert feed<span class="r">each row = delivered once (state/alerts_log.csv)</span></h2><table>{"".join(rows)}</table></div>'


def risk_panel(ctx: dict) -> str:
    rs, c = ctx["risk"], ctx["risk"]["caps"]
    nxt = riskstate.headroom(rs, c["standalone_pct"])
    g = ctx["groups"][0] if ctx["groups"] else None
    daily_used = max(0.0, -rs["daily_pct"]) + rs["open_risk_pct"]
    blue = palette.STATUS["pending"]["fg"]
    out = [meter("Next trade", c["standalone_pct"] if nxt["allowed"] else 0.0, c["standalone_pct"],
                 "standalone 0.5% · correlated 0.25% each" if nxt["allowed"] else E("; ".join(nxt["reasons"])), blue, alarm=False),
           meter(f"{g['group']} group" if g else "Largest correlated group", g["used"] if g else 0.0, c["correlated_group_max_pct"],
                 f"{g['n']} open in group · max 4" if g else "no open trades", palette.LEVEL["fvg"]),
           meter("Daily loss · worst case", daily_used, c["daily_loss_cap_pct"],
                 f"realised {rs['daily_pct']:+.2f}% + open risk {rs['open_risk_pct']:.2f}% · locks at -{c['daily_loss_cap_pct']:g}%", blue),
           meter("Drawdown from peak", rs["drawdown_pct"], c["max_drawdown_pct"],
                 f"equity {rs['equity_pct']:+.2f}% vs start · peak {rs['peak_pct']:+.2f}% · locks at {c['max_drawdown_pct']:g}%", blue)]
    if rs["locked"]:
        lk = rs["lock"]
        box = (f'<div style="margin:14px;padding:12px;border-radius:10px;border:1px solid #4a3a80;background:#261d42;color:#c4b5fd">'
               f'<b>🔒 LOCKED</b> · {E(lk["reason"])}<br>New setups are recorded as LOCKED, not alerted, until {E(lk["until"])}.</div>')
    else:
        box = (f'<div style="margin:14px;padding:12px;border-radius:10px;border:1px solid #235a33;background:#16351f;color:#86efac">'
               f'<b>✓ New alerts allowed</b> · {nxt["daily_left_pct"]:.2f}% daily headroom after open risk<br>'
               f'<span style="color:#a9b2c3">At -{c["daily_loss_cap_pct"]:g}% today or {c["max_drawdown_pct"]:g}% from peak, new setups are LOCKED.</span></div>')
    return (f'<div class="card"><h2>Risk budget<span class="r">model account · {rs["realised_events"]} realised events</span></h2>{"".join(out)}{box}'
            f'<div class="mut" style="padding:0 14px 14px;font-size:11.5px">Counts every alerted setup at its recorded risk %; '
            f'TP1 banks 50%. No broker link - check against your account.</div></div>')


def build_index(ctx: dict, refresh=None) -> str:
    body = (kz_timeline(ctx) + '<div class="grid3">' + watchlist(ctx) + '<div>' + setups_table(ctx) + alert_feed(ctx) + '</div>'
            + risk_panel(ctx) + '</div>')
    return page("Session monitor", "dash", body, ctx, refresh)


# =============================================================== setup detail
def checklist(s: dict) -> list[tuple[str, str, str]]:
    """[(state ok|warn|info|bad, title, detail)] - why the setup qualified, from its own record."""
    d = s.get("decimals", 2)
    sw, m, fvg, ote = s.get("sweep") or {}, s.get("mss") or {}, s.get("fvg") or {}, s.get("ote") or {}
    tps = s.get("targets") or []
    best = max([t.get("r") or 0 for t in tps], default=0)
    val = (s.get("validation") or {}).get("verdict")
    items = [("ok" if s.get("killzone") else "bad", "Kill zone", s.get("killzone") or "missing"),
             ("ok" if sw.get("description") else "bad", "Liquidity sweep",
              f'{sw.get("description", "")} · raid extreme {fmt(sw.get("extreme"), d)}'),
             ("ok" if m.get("description") else "bad", "MSS + displacement", f'{m.get("description", "")} · {m.get("displacement", "")}'),
             ("ok" if fvg else "warn", "FVG in the leg", f'{fmt(fvg.get("low"), d)}–{fmt(fvg.get("high"), d)}' if fvg else "no FVG recorded")]
    in_ote = ote and ote.get("low") is not None and ote["low"] <= (s.get("entry") or 0) <= ote["high"]
    items.append(("ok" if in_ote else "warn", "Entry in OTE", f'entry {fmt(s.get("entry"), d)} · OTE 62–79% {fmt(ote.get("low"), d)}–{fmt(ote.get("high"), d)}'))
    if s.get("unicorn"):
        items.append(("info", "Unicorn", "breaker overlaps the FVG -> model = Unicorn"))
    dflt = any("DEFAULT" in c for c in s.get("caveats", []))
    items.append(("warn" if dflt else "ok", "Stop buffer",
                  f'stop {fmt(s.get("stop"), d)} = raid + spread {s.get("spread")} + 1m wick {s.get("wick")}' + (" · DEFAULT buffer" if dflt else "")))
    items.append(("ok" if best >= 2 and val == "VALID" else "bad", "Min 1:2 R:R",
                  " · ".join(f'{t["name"]} {t.get("r")}R' + (" PARTIAL" if t.get("partial") else "") for t in tps) + f" · validator {val}"))
    lab = s.get("bias_label", "")
    bd = s.get("bias_detail") or {}
    items.append(("warn" if lab == "COUNTER-TREND" else "ok", "MTF bias", f'{lab} · {bd.get("label") or s.get("daily_bias") or "n/a"} (confidence {bd.get("confidence")})'))
    items.append(("ok", "Risk", f'{s.get("risk_pct")}% {s.get("risk_mode", "")} · size {s.get("size_formula", "")}'))
    src = s.get("data_source") or "UNKNOWN"
    items.append(("warn" if s.get("fallback") or "EXAMPLE" in src.upper() else "ok", "Data",
                  ("yfinance FALLBACK · " if s.get("fallback") else "") + src))
    if s.get("status") == "locked":
        items.append(("bad", "Risk lock", (s.get("lock") or {}).get("reason", "LOCKED")))
    return items


def chart_svg(s: dict, bars: list[dict], now: datetime, W=900, H=470) -> str:
    """Annotated 1m chart (inline SVG). Bars up to `now` only, window from ~20 bars before the sweep."""
    d = s.get("decimals", 2)
    sw = s.get("sweep") or {}
    bars = [b for b in bars if datetime.fromisoformat(b["time"]) + timedelta(minutes=1) <= now]
    if not bars:
        return '<div class="mut" style="padding:40px">No cached 1m bars for this setup (state/bars/ is written by watch.py each cycle).</div>'
    times = [b["time"] for b in bars]
    i0 = times.index(sw["extreme_time_iso"]) - 25 if sw.get("extreme_time_iso") in times else len(bars) - 90
    bars = bars[max(0, i0):][-150:]
    times = [b["time"] for b in bars]
    n = len(bars)
    tps = s.get("targets") or []
    lv = [x for x in (s.get("entry"), s.get("stop"), sw.get("extreme"), *(t["price"] for t in tps)) if x is not None]
    lo, hi = min([b["low"] for b in bars] + lv), max([b["high"] for b in bars] + lv)
    pad = (hi - lo) * 0.07 or 1
    lo, hi = lo - pad, hi + pad
    L, R, Tp, B = 10, 130, 14, 40
    pw, ph = W - L - R, H - Tp - B
    X = lambda i: L + (i + 0.5) * pw / n  # noqa: E731
    Y = lambda p: Tp + (hi - p) / (hi - lo) * ph  # noqa: E731
    bw = max(1.5, pw / n * 0.62)
    g = [f'<rect x="0" y="0" width="{W}" height="{H}" fill="{T["bg2"]}"/>']
    for k in range(6):
        y = Tp + k * ph / 5
        g.append(f'<line x1="{L}" x2="{L + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{T["line"]}" stroke-width="0.6"/>')
    x0 = times.index(sw["extreme_time_iso"]) if sw.get("extreme_time_iso") in times else n // 2

    def zone(z, col, label):
        if z and z.get("low") is not None:
            g.append(f'<rect x="{X(x0):.1f}" y="{Y(z["high"]):.1f}" width="{L + pw - X(x0):.1f}" height="{max(1, Y(z["low"]) - Y(z["high"])):.1f}" fill="{col}" fill-opacity=".16" stroke="{col}" stroke-opacity=".5" stroke-width=".6"/>')
            g.append(f'<text x="{L + pw - 4:.1f}" y="{Y(z["high"]) + 11:.1f}" text-anchor="end" fill="{col}" font-size="10.5">{E(label)}</text>')
    zone(s.get("ote"), LV["ote"], "OTE 62–79%")
    zone(s.get("fvg"), LV["fvg"], "FVG")
    for i, b in enumerate(bars):
        col = T["up"] if b["close"] >= b["open"] else T["down"]
        y1, y2 = Y(max(b["open"], b["close"])), Y(min(b["open"], b["close"]))
        g.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{Y(b["high"]):.1f}" y2="{Y(b["low"]):.1f}" stroke="{col}" stroke-width="1"/>'
                 f'<rect x="{X(i) - bw / 2:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(1, y2 - y1):.1f}" fill="{col}"/>')
    if sw.get("level") is not None:
        g.append(f'<line x1="{L}" x2="{L + pw}" y1="{Y(sw["level"]):.1f}" y2="{Y(sw["level"]):.1f}" stroke="{LV["sweep"]}" stroke-dasharray="4 4" stroke-opacity=".7"/>')
    mss = (s.get("mss") or {}).get("level")
    if mss is not None:
        g.append(f'<line x1="{L}" x2="{L + pw}" y1="{Y(mss):.1f}" y2="{Y(mss):.1f}" stroke="{LV["mss"]}" stroke-dasharray="1 3" stroke-opacity=".7"/>')
    tags = [("SL", s.get("stop"), LV["stop"], ""), ("ENTRY", s.get("entry"), LV["entry"], "")] + [(t["name"], t["price"], LV["target"], "5 4") for t in tps]
    placed = []
    for name, y, col, dash in sorted([t for t in tags if t[1] is not None], key=lambda t: -t[1]):
        g.append(f'<line x1="{L}" x2="{L + pw}" y1="{Y(y):.1f}" y2="{Y(y):.1f}" stroke="{col}" stroke-width="1.2" stroke-dasharray="{dash}"/>')
        ty = Y(y)
        for p in placed:
            if abs(ty - p) < 20:
                ty = p + 20
        placed.append(ty)
        r = next((t.get("r") for t in tps if t["name"] == name), None)
        g.append(f'<rect x="{L + pw + 6}" y="{ty - 9:.1f}" width="{R - 10}" height="18" rx="4" fill="{col}"/>'
                 f'<text x="{L + pw + 11}" y="{ty + 4:.1f}" fill="{T["bg"]}" font-size="10.5" font-weight="700">{E(name)}{"" if r is None else f" {r}R"}</text>'
                 f'<text x="{L + pw + R - 8}" y="{ty + 4:.1f}" fill="{T["bg"]}" font-size="10.5" font-weight="700" text-anchor="end" class="mono">{fmt(y, d)}</text>')
    # numbered markers: 1 sweep, 2 MSS, then lifecycle events
    marks = []
    if sw.get("extreme_time_iso") in times:
        marks.append((times.index(sw["extreme_time_iso"]), sw.get("extreme"), "1", "Sweep", LV["sweep"]))
    bt = next((e for e in [(s.get("mss") or {}).get("description", "")[:5]] if e), None)
    if bt:
        cand = [i for i, t in enumerate(times) if t[11:16] == bt and i >= (marks[0][0] if marks else 0)]
        if cand:
            marks.append((cand[0], bars[cand[0]]["close"], "2", "MSS", LV["mss"]))
    k = 3
    for e in s.get("events", []):
        if e.get("event") in ("NEW", None):
            continue
        t = e.get("time", "")
        idx = next((i for i, tt in enumerate(times) if tt >= t), None)
        if idx is not None:
            marks.append((idx, e.get("price") or bars[idx]["close"], str(k), e["event"], palette.colors(e["event"])["fg"])); k += 1
    for i, p, num, label, col in marks:
        cy = Y(p) + (-16 if s["direction"] == "short" and label == "Sweep" else 16 if label == "Sweep" else -16)
        g.append(f'<circle cx="{X(i):.1f}" cy="{cy:.1f}" r="8" fill="{col}"/><text x="{X(i):.1f}" y="{cy + 3.5:.1f}" text-anchor="middle" font-size="10" font-weight="700" fill="{T["bg"]}">{num}</text>'
                 f'<text x="{X(i) + 11:.1f}" y="{cy + 3.5:.1f}" font-size="10.5" fill="{col}">{E(label)}</text>')
    step = max(1, n // 7)
    for i in range(0, n, step):
        t = datetime.fromisoformat(times[i])
        g.append(f'<text x="{X(i):.1f}" y="{H - 22}" text-anchor="middle" font-size="10" fill="{T["mut"]}" class="mono">{t.astimezone(SAST):%H:%M}</text>'
                 f'<text x="{X(i):.1f}" y="{H - 9}" text-anchor="middle" font-size="9.5" fill="{T["mut"]}" opacity=".7" class="mono">{t.astimezone(NY):%H:%M} NY</text>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;font-family:inherit">{"".join(g)}</svg>'


def build_setup(ctx: dict, s: dict) -> str:
    d = s.get("decimals", 2)
    rows, _ = ctx["bars"].get(s["instrument"], ([], {}))
    tps = s.get("targets") or []
    r = r_of(s)
    st = s.get("status", "pending")
    stats = [("Entry", fmt(s.get("entry"), d), LV["entry"]), ("Stop · risk", f'{fmt(s.get("stop"), d)} <span class="mut" style="font-size:12px">{s.get("risk_pts")}</span>', LV["stop"]),
             ("TP1 · TP2", " · ".join(f'{t.get("r")}R' for t in tps[:2]) or "–", LV["target"]),
             ("Risk", f'{s.get("risk_pct")}% <span class="mut" style="font-size:12px">{E(s.get("risk_mode", ""))}</span>', T["tx"]),
             ("Result so far", "–" if r is None else f"{r:+.2f}R", T["up"] if (r or 0) > 0 else T["down"] if (r or 0) < 0 else T["tx2"])]
    head = (f'<div class="mut" style="font-size:12px"><a href="index.html">Setups</a> / {E(s.get("date", ""))} / {E(s.get("killzone", "").split(" (")[0])}</div>'
            f'<div style="display:flex;align-items:center;gap:12px;margin-top:6px"><h1 style="font-size:26px">{E(s["id"])} · {E(s["instrument"])}</h1>{dirtag(s["direction"])}{pill(st)}'
            f'<div style="margin-left:auto;display:flex;gap:10px">'
            + "".join(f'<div class="card stat" style="min-width:130px"><div class="l">{E(k)}</div><div class="v mono" style="color:{c};font-size:17px">{v}</div></div>' for k, v, c in stats)
            + f'</div></div><div class="tx2" style="margin-top:4px">{E(s.get("model", ""))} · Charter {E((s.get("charter_model") or "").split(" ")[0])} · {E(s.get("charter_stage", ""))} · {E(s.get("symbol", ""))} · {E(s.get("timeframe", "1m"))}</div>')
    icon = {"ok": ("✓", "#22c55e", "#16351f"), "warn": ("!", "#f5b041", "#3a2c12"), "info": ("i", "#4f9dff", "#132743"), "bad": ("✕", "#f25555", "#3a1618")}
    cl = "".join(f'<div style="display:flex;gap:10px;padding:9px 14px;border-bottom:1px solid var(--line)"><span style="flex:none;width:20px;height:20px;border-radius:5px;display:grid;place-items:center;font-weight:700;font-size:12px;color:{icon[k][1]};background:{icon[k][2]}">{icon[k][0]}</span>'
                 f'<div><b><span class="mut mono">{i}.</span> {E(t)}</b><div class="tx2" style="font-size:12px">{E(x)}</div></div></div>'
                 for i, (k, t, x) in enumerate(checklist(s), 1))
    warn = [c for c in s.get("caveats", []) if c]
    cav = f'<div style="padding:10px 14px;color:#f5b041;font-size:12px">{"<br>".join("! " + E(c) for c in warn[:3])}</div>' if warn else ""
    evs = [e for e in s.get("events", [])]
    tl = []
    for e in evs:
        t = datetime.fromisoformat(e.get("time") or s["alert_time"]).astimezone(SAST)
        rr = "" if e.get("r") in (None, 0) else f' · {e["r"]:+.2f}R'
        px = "" if e.get("price") is None else f'{fmt(e["price"], d)}'
        tl.append(f'<div style="flex:1;min-width:140px"><div>{pill(e["event"], e["event"])}</div><div class="mono" style="margin-top:6px"><b>{t:%H:%M}</b> <span class="mut">{t.astimezone(NY):%H:%M} NY</span></div>'
                  f'<div class="mut" style="font-size:11.5px">{E(px)}{E(rr)}{" · " + E(e.get("note", "")) if e.get("note") else ""}</div></div>')
    if st == "pending" and s.get("killzone_end"):
        ke = datetime.fromisoformat(s["killzone_end"]).astimezone(SAST)
        tl.append(f'<div style="flex:1;min-width:140px;opacity:.6"><div>{pill("cancelled", "Auto-cancel")}</div><div class="mono" style="margin-top:6px"><b>{ke:%H:%M}</b> <span class="mut">{ke.astimezone(NY):%H:%M} NY</span></div>'
                  '<div class="mut" style="font-size:11.5px">if unfilled at kill-zone end</div></div>')
    elif st in OPEN:
        tl.append(f'<div style="flex:1;min-width:140px;opacity:.7"><div>{pill(st, "Open")}</div><div class="mono" style="margin-top:6px"><b>now</b></div>'
                  '<div class="mut" style="font-size:11.5px">tracking until TP2, stop or breakeven</div></div>')
    if st == "locked":
        t = datetime.fromisoformat(s["alert_time"]).astimezone(SAST)
        tl.append(f'<div style="flex:1;min-width:140px"><div>{pill("locked")}</div><div class="mono" style="margin-top:6px"><b>{t:%H:%M}</b> <span class="mut">{t.astimezone(NY):%H:%M} NY</span></div>'
                  f'<div class="mut" style="font-size:11.5px">found while {E((s.get("lock") or {}).get("reason", "a risk cap was hit"))} · recorded, not alerted</div></div>')
    if not tl:
        tl = ['<div class="mut">No events yet.</div>']
    body = (head + '<div style="display:grid;grid-template-columns:1fr 380px;gap:14px;margin-top:14px">'
            f'<div class="card"><h2>Chart · 1m · times SAST<span class="r"><span style="color:{LV["fvg"]}">■ FVG</span> &nbsp;<span style="color:{LV["ote"]}">■ OTE</span> &nbsp;<span style="color:{LV["entry"]}">■ Entry/sweep</span> &nbsp;<span style="color:{LV["stop"]}">■ Stop</span> &nbsp;<span style="color:{LV["target"]}">■ Targets</span></span></h2>'
            f'<div style="padding:8px">{chart_svg(s, rows, ctx["now"])}</div></div>'
            f'<div class="card"><h2>Why it qualified<span class="r">validate_setup.py {E(str((s.get("validation") or {}).get("verdict")))}</span></h2>{cl}{cav}</div></div>'
            f'<div class="card" style="margin-top:14px;padding:16px 18px;display:flex;gap:18px">{"".join(tl)}</div>')
    return page(f"Setup {s['id']}", "dash", body, ctx)


# =============================================================== lifecycle
NODES = {  # key: (x, y, title, status key for colour, lines, statuses counted)
    "scan": (30, 40, "SCAN", "pending", ["every {every} min · closed 1m bars", "{watched}"], ()),
    "detect": (270, 40, "DETECT", "locked", ["sweep -> MSS with displacement", "-> FVG in leg -> OTE 62–79%"], ()),
    "gate": (510, 40, "GATE", "tp", ["stop >= raid + spread + wick", "min 1:2 R:R (unrounded)", "caps: group 1% · day 2% · DD 5%"], ()),
    "locked": (800, 40, "LOCKED", "locked", ["daily -2% or drawdown 5% hit", "recorded, never alerted · 1 lock alert"], ("locked",)),
    "pending": (40, 210, "PENDING", "pending", ["waiting for the entry", "NEW alert sent (chart first)"], ("pending",)),
    "filled": (380, 210, "FILLED", "filled", ["entry touched on a closed bar", "fill + stop same bar -> STOP"], ("filled",)),
    "tp1": (700, 210, "TP1 · PARTIAL", "tp", ["banks 50% at TP1", "rest keeps the stop (or BE)"], ("tp1", "closed_tp1")),
    "tp2": (950, 380, "TP2", "tp", ["runner target"], ("closed_tp2", "closed_tp3")),
    "be": (965, 210, "BE", "be", ["stop moved to entry"], ("closed_be",)),
    "stop": (700, 540, "STOP", "stop", ["stop first if a bar hits both", "gap -> filled at the open"], ("closed_stop",)),
    "void": (20, 540, "VOID", "void", ["TP1 traded before the fill"], ("void",)),
    "invalid": (240, 540, "INVALID", "invalid", ["raid extreme broke before fill"], ("invalid",)),
    "cancelled": (460, 540, "CANCELLED", "cancelled", ["clock >= kill-zone end, unfilled"], ("cancelled",)),
}
EDGES = [("scan", "detect", ""), ("detect", "gate", ""), ("gate", "locked", "cap hit"), ("gate", "pending", "VALID + gate PASS"),
         ("pending", "filled", "entry touched"), ("filled", "tp1", "TP1 hit"), ("tp1", "tp2", "TP2 hit"), ("tp1", "be", "BE"),
         ("filled", "stop", "stop hit"), ("tp1", "stop", "rest stopped"), ("pending", "void", "TP1 first"),
         ("pending", "invalid", "raid breaks"), ("pending", "cancelled", "kill zone ends")]


def build_lifecycle(ctx: dict) -> str:
    cnt = defaultdict(int)
    for s in ctx["setups"]:
        cnt[s.get("status", "pending")] += 1
    every = ctx["cfg"].get("scan_interval_minutes", 6)
    w, h = 210, 96
    svg = [f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="{T["mut"]}"/></marker></defs>']
    for a, b, lab in EDGES:
        xa, ya = NODES[a][0] + w / 2, NODES[a][1] + h / 2
        xb, yb = NODES[b][0] + w / 2, NODES[b][1] + h / 2
        # leave from / arrive at the box edge
        def edge(x, y, tx, ty):
            dx, dy = tx - x, ty - y
            k = min((w / 2) / abs(dx) if dx else 9e9, (h / 2) / abs(dy) if dy else 9e9)
            return x + dx * k, y + dy * k
        x1, y1 = edge(xa, ya, xb, yb)
        x2, y2 = edge(xb, yb, xa, ya)
        svg.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{T["mut"]}" stroke-width="1.2" marker-end="url(#a)"/>')
        if lab:
            anchor = "middle" if abs(y2 - y1) < 5 else "start"
            svg.append(f'<text x="{(x1 + x2) / 2 + (0 if anchor == "middle" else 6):.0f}" y="{(y1 + y2) / 2 - 6:.0f}" text-anchor="{anchor}" fill="{T["tx2"]}" font-size="11" font-weight="600">{E(lab)}</text>')
    for k, (x, y, title, col, lines, counted) in NODES.items():
        c = palette.STATUS[col]
        n = sum(cnt[s] for s in counted)
        lines = [ln.format(every=every, watched=" & ".join(ctx["watched"])) for ln in lines]
        hl = n > 0 and k in ("pending", "filled", "tp1", "locked")
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{T["panel"]}" stroke="{c["fg"]}" stroke-width="{2.2 if hl else 1.1}" stroke-opacity="{1 if hl or counted == () else .6}"/>'
                   f'<circle cx="{x + 16}" cy="{y + 20}" r="4.5" fill="{c["fg"]}"/><text x="{x + 27}" y="{y + 24}" fill="{c["fg"]}" font-size="12.5" font-weight="700">{E(title)}</text>'
                   + (f'<text x="{x + w - 12}" y="{y + 24}" text-anchor="end" fill="{T["tx"]}" font-size="12" font-weight="700" class="mono">{n}</text>' if counted else "")
                   + "".join(f'<text x="{x + 14}" y="{y + 46 + i * 16}" fill="{T["tx2"]}" font-size="11">{E(ln)}</text>' for i, ln in enumerate(lines)))
    trans = []
    for s in ctx["setups"]:
        for e in s.get("events", []):
            trans.append((e.get("detected_at") or e.get("time") or "", s, e))
    trans.sort(key=lambda x: x[0], reverse=True)
    rows = "".join(f'<tr><td class="mono">{datetime.fromisoformat(t).astimezone(SAST):%a %d %b %H:%M}</td><td class="mono"><a href="setup-{E(s["id"])}.html"><b>{E(s["id"])}</b></a> {E(s["instrument"])}</td>'
                   f'<td>{pill(e["event"], e["event"])}</td><td class="mono">{rtxt(e)}</td><td class="mut">{E(e.get("note", "") or "")}</td></tr>'
                   for t, s, e in trans[:14] if t)
    locked_note = ""
    if ctx["risk"]["locked"]:
        locked_note = f'<span class="pill p-locked"><i></i>LOCKED now · {E(ctx["risk"]["lock"]["reason"])}</span>'
    legend = " ".join(pill(k, palette.STATUS[k]["label"]) for k in ("pending", "filled", "tp", "stop", "be", "cancelled", "locked"))
    body = (f'<div style="display:flex;align-items:flex-end;gap:16px"><div><h1 style="font-size:24px">Setup lifecycle &amp; alert triggers</h1>'
            f'<div class="tx2" style="margin-top:4px">Every transition is decided by lifecycle.py on closed bars and sends exactly one alert. '
            f'Numbers in the boxes are live counts from state/setups.</div></div><div style="margin-left:auto;text-align:right">{legend}<div style="margin-top:6px">{locked_note}</div></div></div>'
            f'<div class="card" style="margin-top:14px;padding:10px"><svg viewBox="0 0 1180 660" width="100%" style="display:block">{"".join(svg)}</svg></div>'
            f'<div class="card" style="margin-top:14px"><h2>Recent transitions<span class="r">newest first · from the setup records</span></h2>'
            f'<table><tr><th>Seen (SAST)</th><th>Setup</th><th>Event</th><th>R</th><th>Note</th></tr>{rows or "<tr><td class=mut>none yet</td></tr>"}</table></div>')
    return page("Lifecycle", "life", body, ctx)


def rtxt(e: dict) -> str:
    r = e.get("r")
    if r is None or e.get("event") in ("NEW", "FILLED"):
        return ""
    cls = "ok" if r > 0 else "bad" if r < 0 else "mut"
    return f'<b class="{cls}">{r:+.2f}R</b>'


# =============================================================== weekly recap
CT_TAG = '<span class="tag" style="color:#f5b041;background:#3a2c12">COUNTER-TREND</span>'


def week_bounds(day: date) -> tuple[date, date]:
    mon = day - timedelta(days=day.weekday())
    return mon, mon + timedelta(days=6)


def build_recap(ctx: dict, week_of: date | None = None, prereg: dict | None = None) -> str:
    week_of = week_of or ctx["now"].date()
    mon, sun = week_bounds(week_of)
    first = {}
    for row in ctx["log"]:
        k = (row["date"], row["setup_id"])
        first[k] = min(first.get(k, "99:99"), row.get("alert_time_sast") or "99:99")
    allres = sorted(recap.setup_results(ctx["log"]), key=lambda r: (r["date"], first.get((r["date"], r["setup_id"]), ""), r["setup_id"]))
    res = [r for r in allres if mon.isoformat() <= r["date"] <= sun.isoformat()]
    closed = [r for r in res if r["taken"] and r["r"] is not None]
    nott = [r for r in res if not r["taken"] and r["outcome"] in ("VOID", "INVALID", "CANCELLED")]
    still = [r for r in res if r["outcome"] == "OPEN"]
    proof = proof_status(allres, prereg)
    net = round(sum(r["r"] for r in closed), 2)
    wins, losses = sum(r["r"] > 0 for r in closed), sum(r["r"] < 0 for r in closed)
    cum, peak, mdd, pts = 0.0, 0.0, 0.0, [0.0]
    for r in closed:
        cum += r["r"]; peak = max(peak, cum); mdd = min(mdd, cum - peak); pts.append(round(cum, 2))
    risk_each = ctx["cfg"]["risk"]["standalone_pct"]
    cards = [("Net R", f'<span class="{"ok" if net > 0 else "bad" if net < 0 else ""}">{net:+.2f}R</span>', f"≈ {net * risk_each:+.2f}% at {risk_each:g}%/R"),
             ("Closed trades", str(len(closed)), f"{wins} W · {losses} L"),
             ("Avg R / trade", f"{(net / len(closed)) if closed else 0:+.2f}R", "closed, taken trades"),
             ("Max drawdown", f'<span class="bad">{mdd:.2f}R</span>', f"cap {ctx['cfg']['risk'].get('max_drawdown_pct', 5):g}% ≈ {ctx['cfg']['risk'].get('max_drawdown_pct', 5) / risk_each:.0f}R"),
             ("Not taken", str(len(nott)), " · ".join(f"{sum(r['outcome'] == k for r in nott)} {k.lower()}" for k in ("VOID", "INVALID", "CANCELLED"))),
             ("Still open", str(len(still)), ", ".join(r["setup_id"] for r in still) or "none")]
    head_cards = "".join(f'<div class="card stat"><div class="l">{k}</div><div class="v mono">{v}</div><div class="mut" style="font-size:11.5px">{E(sub)}</div></div>' for k, v, sub in cards)
    # cumulative R chart
    W, H, L, B = 760, 260, 44, 46
    lo, hi = min(pts + [-1]) - 0.5, max(pts + [1]) + 0.5
    X = lambda i: L + i * (W - L - 50) / max(1, len(pts) - 1)  # noqa: E731
    Y = lambda v: 14 + (hi - v) / (hi - lo) * (H - 14 - B)  # noqa: E731
    g = []
    for v in range(math.floor(lo), math.ceil(hi) + 1):
        g.append(f'<line x1="{L}" x2="{W - 10}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{T["line"]}" stroke-width=".6"/><text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="{T["mut"]}" font-size="10" class="mono">{v:+d}R</text>')
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(pts))
    g.append(f'<polygon points="{X(0):.1f},{Y(0):.1f} {line} {X(len(pts) - 1):.1f},{Y(0):.1f}" fill="{palette.STATUS["pending"]["fg"]}" fill-opacity=".12"/>'
             f'<polyline points="{line}" fill="none" stroke="{palette.STATUS["pending"]["fg"]}" stroke-width="2"/>')
    for i, r in enumerate(closed, 1):
        col = palette.colors("TP1" if r["r"] > 0 else "STOP" if r["r"] < 0 else "BE")["fg"]
        g.append(f'<circle cx="{X(i):.1f}" cy="{Y(pts[i]):.1f}" r="5" fill="{T["bg"]}" stroke="{col}" stroke-width="2"/>'
                 f'<text x="{X(i):.1f}" y="{Y(pts[i]) + (-11 if r["r"] >= 0 else 18):.1f}" text-anchor="middle" fill="{col}" font-size="10.5" font-weight="700" class="mono">{r["r"]:+.2f}</text>'
                 f'<text x="{X(i):.1f}" y="{H - 26}" text-anchor="middle" fill="{T["tx2"]}" font-size="10">{E(r["instrument"])}</text>'
                 f'<text x="{X(i):.1f}" y="{H - 12}" text-anchor="middle" fill="{T["mut"]}" font-size="9.5">{datetime.fromisoformat(r["date"]):%a %d} · {E("LO" if r["session"].startswith("London") else r["session"])}</text>')
    chart = f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block">{"".join(g)}</svg>' if closed else '<div class="mut" style="padding:30px">No closed trades this week.</div>'

    def bars_by(key, title):
        agg = recap.summarise(res, key)
        if not agg:
            return f'<div class="card" style="margin-bottom:14px"><h2>{title}</h2><div class="mut" style="padding:14px">none</div></div>'
        mx = max(abs(a["total_r"]) for a in agg.values()) or 1
        rows = []
        for k, a in sorted(agg.items(), key=lambda kv: -kv[1]["total_r"]):
            v = a["total_r"]
            col = palette.colors("TP1" if v > 0 else "STOP" if v < 0 else "BE")["fg"]
            wpx = 46 * abs(v) / mx
            bar = (f'<div style="position:relative;height:8px;background:var(--line);border-radius:4px;width:100%">'
                   f'<div style="position:absolute;top:0;bottom:0;{"left:50%" if v >= 0 else "right:50%"};width:{wpx:.1f}%;background:{col};border-radius:4px"></div></div>')
            rows.append(f'<tr><td style="width:120px">{E(k)}</td><td>{bar}</td><td class="mono" style="width:70px;text-align:right;color:{col}"><b>{v:+.2f}R</b></td>'
                        f'<td class="mut mono" style="width:110px">{a["wins"]}W {a["losses"]}L · n={a["trades"]}</td></tr>')
        return f'<div class="card" style="margin-bottom:14px"><h2>{title}</h2><table>{"".join(rows)}</table></div>'

    trades = "".join(
        f'<tr><td class="mono">{datetime.fromisoformat(r["date"]):%a %d}</td><td>{E(r["session"])}</td><td><b>{E(r["instrument"])}</b> {dirtag(r["direction"])}</td>'
        f'<td>{E(r["model"].split(" (")[0])}</td><td>{CT_TAG if r["label"] == "COUNTER-TREND" else E(r["label"])}</td>'
        f'<td>{pill(r["outcome"] if r["outcome"] != "OPEN" else "filled", r["outcome"] + (" (gap)" if r["outcome"] == "STOP" and (r["r"] or 0) < -1 else ""))}</td>'
        f'<td class="mono" style="text-align:right">{rcell(r["r"]).replace("<br>", "") if r["r"] is not None else "–"}</td></tr>' for r in res)
    notes = []
    sess = recap.summarise(res, "session")
    if sess:
        worst = min(sess.items(), key=lambda kv: kv[1]["total_r"])
        notes.append(f'{worst[0]} is {worst[1]["wins"]}W {worst[1]["losses"]}L ({worst[1]["total_r"]:+.2f}R) this week. With n = {worst[1]["trades"]} this is noise until a pre-registered test says otherwise.')
    ct = [r for r in closed if r["label"] == "COUNTER-TREND"]
    if ct:
        notes.append(f"{len(ct)} counter-trend trade(s): {sum(r['r'] for r in ct):+.2f}R. Allowed by the rules, always labelled.")
    gaps = [r for r in closed if r["r"] < -1]
    if gaps:
        notes.append(f"{len(gaps)} stop(s) gapped past -1R (worst {min(r['r'] for r in gaps):+.2f}R): size from the stop, expect a gap tail.")
    badge_col = {"unproven": ("#f5b041", "#3a2c12", "#5a4318"), "meets": ("#22c55e", "#16351f", "#235a33"), "fails": ("#f25555", "#3a1618", "#5e2326")}[proof["key"]]
    badge = (f'<div style="display:flex;gap:12px;align-items:center;max-width:470px;padding:12px 14px;border-radius:10px;border:1px solid {badge_col[2]};background:{badge_col[1]}">'
             f'<span style="flex:none;padding:6px 12px;border-radius:7px;background:{badge_col[0]};color:#090c12;font-weight:800;letter-spacing:.06em">{E(proof["badge"])}</span>'
             f'<span style="color:{badge_col[0]};font-size:12px">{E(proof["reason"])}</span></div>')
    body = (f'<div style="display:flex;gap:16px;align-items:flex-start"><div><h1 style="font-size:26px">Weekly recap · Week {mon.isocalendar()[1]}</h1>'
            f'<div class="tx2" style="margin-top:4px">{mon:%a %d} – {sun:%a %d %b %Y} · setups from state/alerts_log.csv · R per setup (TP1 banks 50%) · times SAST, kill zones New York-defined</div></div>'
            f'<div style="margin-left:auto">{badge}</div></div>'
            f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-top:16px">{head_cards}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 380px;gap:14px;margin-top:14px"><div>'
            f'<div class="card"><h2>Cumulative R · closed trades in order<span class="r">green = win · red = loss</span></h2><div style="padding:10px">{chart}</div></div>'
            f'<div class="card" style="margin-top:14px"><h2>Trades<span class="r">from state/alerts_log.csv</span></h2><table><tr><th>Day</th><th>Kill zone</th><th>Instrument</th><th>Model</th><th>Bias label</th><th>Outcome</th><th style="text-align:right">R</th></tr>{trades or "<tr><td class=mut>none</td></tr>"}</table></div></div>'
            f'<div>{bars_by("session", "By kill zone")}{bars_by("instrument", "By instrument")}{bars_by("label", "By bias label")}'
            f'<div class="card"><h2>What to look at next</h2><div style="padding:12px 14px;font-size:12.5px" class="tx2">{"<br>".join("• " + E(n) for n in notes) or "Nothing stands out."}</div></div></div></div>')
    return page(f"Recap week {mon.isocalendar()[1]}", "recap", body, ctx)


# =============================================================== build / serve / example
def build(root: str = ROOT, out: str | None = None, now: datetime | None = None, cfg: dict | None = None,
          bars_dir: str | None = None, week_of: date | None = None, refresh: int | None = None) -> dict:
    now = (now or datetime.now(SAST)).astimezone(SAST)
    out = out or os.path.join(root, "dashboard")
    ctx = collect(root, now, cfg, bars_dir)
    prereg = _read_json(os.path.join(root, "config", "preregistration.json"))
    os.makedirs(out, exist_ok=True)
    files = {"index.html": build_index(ctx, refresh), "lifecycle.html": build_lifecycle(ctx), "recap.html": build_recap(ctx, week_of, prereg)}
    for s in ctx["setups"]:
        files[f"setup-{s['id']}.html"] = build_setup(ctx, s)
    for name, text in files.items():
        with open(os.path.join(out, name), "w") as f:
            f.write(text)
    return {"out": out, "pages": sorted(files), "example": ctx["example"], "locked": ctx["risk"]["locked"]}


def serve(root: str, port: int, bars_dir: str | None = None, now: datetime | None = None) -> None:
    """Tiny local server (127.0.0.1 only). Every page load rebuilds the pages from the current state."""
    import http.server, functools
    out = os.path.join(root, "dashboard")

    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "") or self.path.split("?")[0].endswith(".html"):
                build(root, out, now, bars_dir=bars_dir, refresh=60)
            return super().do_GET()

    build(root, out, now, bars_dir=bars_dir, refresh=60)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(H, directory=out))
    print(f"dashboard on http://127.0.0.1:{port}/  (Ctrl+C to stop; pages rebuild on each load and auto-refresh every 60 s)")
    srv.serve_forever()


def seed_example(root: str, until: str = "09:40") -> datetime:
    """EXAMPLE DATA: fabricated week (examples/EXAMPLE_week.json) + the real watch cycle replayed on the fabricated
    15 Jan 2026 bars (dry run - nothing is sent; the NEW/FILLED/TP1 rows are written to the log as if delivered so the
    feed and recap have rows). Every record is marked example=true and 'EXAMPLE DATA (fabricated)'."""
    import watch, alert_log
    os.makedirs(os.path.join(root, "state", "setups"), exist_ok=True)
    bars = os.path.join(root, "bars")
    os.makedirs(bars, exist_ok=True)
    for iv in ("1m", "1d"):
        shutil.copy(os.path.join(ROOT, "examples", f"EXAMPLE_bars_NAS100_{iv}.csv"), os.path.join(bars, f"NAS100_{iv}.csv"))
        with open(os.path.join(bars, f"NAS100_{iv}.csv.meta.json"), "w") as f:
            json.dump({"source": "EXAMPLE DATA (fabricated) NAS100", "fallback": False}, f)
    log = os.path.join(root, "state", "alerts_log.csv")
    fx = _read_json(os.path.join(ROOT, "examples", "EXAMPLE_week.json"))
    cfg = emconfig.load_config()
    for c in fx["setups"]:
        spec = cfg["instruments"][c["instrument"]]
        d = spec["decimals"]
        tday = date.fromisoformat(c["date"])
        kz = next(k for k in killzone.killzones_for(tday) if k["name"] == c["kz"])
        risk = abs(c["stop"] - c["entry"])
        first = datetime.combine(tday, datetime.strptime(c["events"][0][1], "%H:%M").time(), SAST)
        new_t = first - timedelta(minutes=3)
        sign = 1 if c["direction"] == "short" else -1
        tg = [{"name": f"TP{i}", "price": p, "r": round(abs(c["entry"] - p) / risk, 2), "label": "EXAMPLE pool"} for i, p in enumerate(c["targets"], 1)]
        s = {"id": c["id"], "date": c["date"], "instrument": c["instrument"], "symbol": spec["tv"], "decimals": d, "timeframe": "1m",
             "direction": c["direction"], "killzone": f"{kz['name']} ({kz['ny']} NY = {kz['start_sast']:%H:%M}-{kz['end_sast']:%H:%M} SAST)",
             "killzone_end": kz["end_sast"].isoformat(), "killzone_end_sast": f"{kz['end_sast']:%H:%M}", "model": c["model"],
             "charter_model": "", "charter_stage": "", "bias_label": c["label"], "daily_bias": "bear" if c["direction"] == "short" else "bull",
             "sweep": {"description": "EXAMPLE sweep", "level": round(c["stop"] - sign * risk * 0.4, d), "extreme": round(c["stop"] - sign * risk * 0.25, d)},
             "mss": {"description": "EXAMPLE MSS", "level": round(c["entry"] - sign * risk * 0.6, d), "displacement": "EXAMPLE"},
             "fvg": None, "ote": None, "entry": c["entry"], "stop": c["stop"], "risk_pts": round(risk, d + 2), "targets": tg,
             "risk_pct": 0.5, "risk_mode": "standalone", "caveats": [], "validation": {"verdict": "VALID", "errors": [], "notes": []},
             "data_source": "EXAMPLE DATA (fabricated)", "fallback": False, "example": True, "status": c["status"],
             "alert_time": new_t.isoformat(), "events": [{"event": "NEW", "time": new_t.isoformat(), "price": None, "r": None, "note": ""}]}
        for ev, hhmm, r in c["events"]:
            t = datetime.combine(tday, datetime.strptime(hhmm, "%H:%M").time(), SAST).isoformat()
            px = {"FILLED": c["entry"], "STOP": c["stop"], "BE": c["entry"], "TP1": c["targets"][0], "TP2": c["targets"][-1]}.get(ev)
            s["events"].append({"event": ev, "time": t, "price": px, "r": None if ev == "FILLED" else r, "note": "EXAMPLE", "detected_at": t})
        with open(os.path.join(root, "state", "setups", f"{c['id']}.json"), "w") as f:
            json.dump(s, f, indent=2)
        for e in s["events"]:
            alert_log.add(log, {"date": s["date"], "session": c["kz"], "instrument": s["instrument"], "setup_id": s["id"], "direction": s["direction"],
                                "label": s["bias_label"], "model": s["model"], "event": e["event"], "alert_time_sast": e["time"][11:16],
                                "swept": "EXAMPLE", "mss": "EXAMPLE", "zone": f"entry {s['entry']}", "stop": s["stop"],
                                "target": " / ".join(str(t["price"]) for t in tg), "r": "" if e["r"] is None else e["r"], "chart": "EXAMPLE"})
    # real watch cycle on the fabricated bars (dry run: nothing sent); log rows added as if delivered
    last = None
    for hhmm in ("09:20", "09:27", "09:32", until):
        now = datetime.fromisoformat(f"2026-01-15T{hhmm}:00+02:00")
        rep = watch.run_cycle(now, root=root, bars_dir=bars, dry_run=True, instruments=["NAS100"])
        for x in rep["outbox"]["dry_run"]:
            s = _read_json(os.path.join(root, "state", "setups", f"{x['id']}.json"))
            if not s:
                continue
            s["example"] = True
            with open(os.path.join(root, "state", "setups", f"{x['id']}.json"), "w") as f:
                json.dump(s, f, indent=2)
            ev = next(e for e in s["events"] if e["event"] == x["event"])
            alert_log.add(log, {"date": s["date"], "session": s["killzone"].split(" (")[0], "instrument": s["instrument"], "setup_id": s["id"],
                                "direction": s["direction"], "label": s.get("bias_label", ""), "model": s.get("model", ""), "event": x["event"],
                                "alert_time_sast": (ev.get("detected_at") or ev["time"])[11:16], "swept": s["sweep"].get("description", ""),
                                "mss": s["mss"].get("description", ""), "zone": f"entry {s['entry']}", "stop": s["stop"],
                                "target": " / ".join(str(t["price"]) for t in s["targets"]), "r": "" if ev.get("r") is None else ev["r"],
                                "chart": x.get("chart") or ""})
        last = now
    return last


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=ROOT); p.add_argument("--out"); p.add_argument("--now"); p.add_argument("--bars-dir")
    p.add_argument("--week", help="any date in the recap week (default: this week)")
    p.add_argument("--serve", action="store_true"); p.add_argument("--port", type=int, default=8765)
    p.add_argument("--example", action="store_true", help="build from EXAMPLE (fabricated) data in a temp folder")
    a = p.parse_args(argv)
    now = datetime.fromisoformat(a.now).astimezone(SAST) if a.now else None
    root = a.root
    if a.example:
        root = a.out or tempfile.mkdtemp(prefix="emdash-example-")
        if os.path.exists(os.path.join(root, "state")):
            shutil.rmtree(os.path.join(root, "state"))
        now = now or seed_example(root)
        a.out = os.path.join(root, "dashboard")
    week = date.fromisoformat(a.week) if a.week else None
    if a.serve:
        serve(root, a.port, a.bars_dir, now); return 0
    res = build(root, a.out, now, bars_dir=a.bars_dir, week_of=week)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
