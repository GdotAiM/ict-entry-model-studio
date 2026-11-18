#!/usr/bin/env python3
"""Render an alert (new setup or status change) from a setup JSON. Wording is fixed here so every alert
carries the same fields; the alert-writer agent may add one short context sentence, never remove fields.

    python3 .claude/skills/alert-format/scripts/render_alert.py --setup state/setups/N6.json --event NEW
    python3 .claude/skills/alert-format/scripts/render_alert.py --setup state/setups/N6.json --event TP1 \
        --time 11:17 --price 28159.4 [--note "1m low 28145.0"]
Events: NEW FILLED TP1 TP2 TP3 STOP BE VOID INVALID CANCELLED UPDATE
"""
from __future__ import annotations
import argparse, json, sys

DISCLAIMER = "Analysis only, not financial advice. Nothing here places or manages orders."
STAGE = {"NEW": "Trade Planning", "FILLED": "Trade Execution", "UPDATE": "Trade Management", "TP1": "Trade Management",
         "TP2": "Trade Management (closed)", "TP3": "Trade Management (closed)", "STOP": "Trade Management (closed)",
         "BE": "Trade Management (closed)", "VOID": "Trade Planning (cancelled)", "INVALID": "Trade Planning (cancelled)",
         "CANCELLED": "Trade Planning (cancelled)", "LOCKED": "Trade Planning (locked: risk cap)"}


def _fmt(x, d):
    return "n/a" if x is None else f"{x:.{d}f}"


def label_text(s: dict) -> str:
    lab = s.get("bias_label", "no clear bias")
    return "COUNTER-TREND" if lab == "COUNTER-TREND" else lab


def data_line(s: dict) -> str:
    src = s.get("data_source") or "UNKNOWN SOURCE - do not send"
    if s.get("fallback"):
        return f"Data: ⚠️ yfinance FALLBACK used ({src}). Proxy prices may differ from the CFD you trade - check your chart."
    tail = (" Chart prices may be bid or mid; check your broker's ask against the stop."
            if any(f in src for f in ("OANDA", "CAPITALCOM")) else "")
    return f"Data: {src}.{tail}"


def targets_text(s: dict, d: int) -> str:
    parts = []
    for t in s.get("targets", []):
        tag = " PARTIAL only" if t.get("partial") else ""
        lbl = f" ({t['label']})" if t.get("label") else ""
        parts.append(f"{t['name']} {_fmt(t['price'], d)}{lbl} = {t['r']}R{tag}")
    if s.get("runner"):
        parts.append(f"runner toward {_fmt(s['runner']['price'], d)} ({s['runner'].get('label', '')})".rstrip(" ()"))
    return "; ".join(parts)


def render(s: dict, event: str, time: str | None = None, price: float | None = None, note: str = "") -> str:
    d = s.get("decimals", 2)
    inst, side, sid = s["instrument"], s["direction"], s["id"]
    lab = label_text(s)
    head_lab = "COUNTER-TREND" if lab == "COUNTER-TREND" else lab
    stage = s.get("charter_stage_override") or STAGE[event]
    order = "sell limit" if side == "short" else "buy limit"
    tp1 = s["targets"][0]["price"] if s.get("targets") else None
    ext = s["sweep"]["extreme"]
    L = []
    if event == "NEW":
        L.append(f"🔔 New {inst} {side} ({sid}), {head_lab}")
        L.append(f"Timeframe: {s.get('timeframe', '1m')} | Kill zone: {s.get('killzone', 'n/a')}")
        L.append(f"Model: {s.get('model', 'n/a')} | Charter: {s.get('charter_model', 'n/a')} | Charter stage: {stage}")
        sw = s["sweep"]
        L.append(f"Sweep: {sw.get('description') or ''} (level {_fmt(sw.get('level'), d)}, extreme {_fmt(ext, d)} at {sw.get('extreme_time', 'n/a')})".replace("Sweep:  (", "Sweep: ("))
        m = s.get("mss", {})
        L.append(f"MSS: {m.get('description', 'n/a')} | displacement: {m.get('displacement', 'n/a')} | FVG {_fmt((s.get('fvg') or {}).get('low'), d)}-{_fmt((s.get('fvg') or {}).get('high'), d)}")
        ote = s.get("ote") or {}
        L.append(f"Entry: {order} {_fmt(s['entry'], d)} (OTE 62-79% {_fmt(ote.get('low'), d)}-{_fmt(ote.get('high'), d)})")
        L.append(f"Stop: {_fmt(s['stop'], d)} (raid extreme {_fmt(ext, d)} + spread {s.get('spread')} + 1m wick {s.get('wick')}) | Risk: {s.get('risk_pts')} pts")
        L.append(f"Targets: {targets_text(s, d)}")
        L.append(f"Size: {s.get('size_formula', 'equity x risk% / risk pts')} ({s.get('risk_pct', 0.5)}% {s.get('risk_mode', 'standalone')})")
        L.append("Confluence: " + ("; ".join(s.get("confluence", [])) or "none noted"))
        cav = list(s.get("caveats", []))
        cav.append(f"skip if TP1 {_fmt(tp1, d)} trades before the fill; drop if {_fmt(ext, d)} breaks first"
                   + (f"; cancel if unfilled by {s['killzone_end_sast']} SAST" if s.get("killzone_end_sast") else ""))
        L.append("Caveats: " + "; ".join(cav))
    else:
        t = f" at {time} SAST" if time else ""
        px = _fmt(price, d) if price is not None else None
        r = next((e.get("r") for e in reversed(s.get("events", [])) if e.get("event") == event), None)
        rtxt = f" ({'+' if r and r > 0 else ''}{r}R)" if r is not None else ""
        msg = {
            "FILLED": f"🔔 {inst} {side} ({sid}) FILLED at {_fmt(s['entry'], d)}{t}. Stop {_fmt(s['stop'], d)}, targets {targets_text(s, d)}.",
            "TP1": f"✅ {inst} {side} ({sid}) hit TP1 {px or _fmt(tp1, d)}{t}{rtxt}. Stop {_fmt(s['stop'], d)} untouched; remaining targets open. Moving the stop to breakeven is your call.",
            "TP2": f"✅ {inst} {side} ({sid}) hit TP2 {px}{t}{rtxt}. Trade closed.",
            "TP3": f"✅ {inst} {side} ({sid}) hit TP3 {px}{t}{rtxt}. Trade closed.",
            "STOP": f"❌ {inst} {side} ({sid}) STOPPED at {px or _fmt(s['stop'], d)}{t}{rtxt or ' (-1R)'}.",
            "BE": f"➖ {inst} {side} ({sid}) closed at breakeven {_fmt(s['entry'], d)}{t} (0R on the remaining position).",
            "VOID": f"🚫 {inst} {side} ({sid}) VOID{t}: TP1 {_fmt(tp1, d)} traded before the fill. Cancel any resting {order} at {_fmt(s['entry'], d)}. 0R.",
            "INVALID": f"🚫 {inst} {side} ({sid}) INVALID{t}: the raid extreme {_fmt(ext, d)} broke before the fill. Cancel any resting {order}. 0R.",
            "CANCELLED": f"🚫 {inst} {side} ({sid}) CANCELLED{t}: kill zone ended unfilled. Cancel any resting {order} at {_fmt(s['entry'], d)}. 0R.",
            "UPDATE": f"⚠️ {inst} {side} ({sid}) update{t}: {px or ''}".rstrip(": "),
        }[event]
        L.append(msg + (f" [{head_lab}]" if head_lab == "COUNTER-TREND" else ""))
        if note:
            L.append(note)
        L.append(f"Model: {s.get('model', 'n/a')} | Charter stage: {stage}")
    L.append(data_line(s))
    L.append(DISCLAIMER)
    return "\n".join(L)


def render_lock(rs: dict) -> str:
    """The one alert sent when the daily-loss or drawdown cap is hit (key: lock id + LOCKED in the alert log)."""
    lk, c = rs["lock"], rs["caps"]
    L = [f"🔒 RISK LOCKED: {lk['reason']}.",
         f"New setups are blocked until {lk['until']}. They are still recorded as LOCKED on the dashboard, never alerted.",
         f"Today {rs['daily_pct']:+.2f}% (cap -{c['daily_loss_cap_pct']:g}%) | drawdown {rs['drawdown_pct']:.2f}% (max {c['max_drawdown_pct']:g}%)"
         f" | open risk {rs['open_risk_pct']:.2f}% on {len(rs['open_setups'])} setup(s), still tracked. Any resting orders are yours to review.",
         "Charter stage: Trade Planning (locked: risk cap)",
         "Data: risk state from the alerted setups at their recorded risk % (model account, no broker link).",
         DISCLAIMER]
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", required=True); p.add_argument("--event", required=True, choices=sorted(set(STAGE) - {"LOCKED"}))
    p.add_argument("--time"); p.add_argument("--price", type=float); p.add_argument("--note", default="")
    p.add_argument("--out")
    a = p.parse_args(argv)
    with open(a.setup) as f:
        s = json.load(f)
    try:
        txt = render(s, a.event, a.time, a.price, a.note)
    except KeyError as e:
        print(f"error: setup missing field {e}", file=sys.stderr); return 2
    if a.out:
        with open(a.out, "w") as f:
            f.write(f"<!-- event: {a.event} setup: {s['id']} -->\n" + txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
