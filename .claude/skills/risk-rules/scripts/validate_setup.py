#!/usr/bin/env python3
"""Hilda's stop / R:R / labelling rules, applied deterministically. The model never does this maths in its head.

Rules:
  1. Stop must sit at least (spread + normal 1m wick) beyond the raid extreme. If no stop is given, that minimum is used.
  2. Risk = |entry - stop|. Every target is expressed in R.
  3. Minimum 1:2. TP1 under 2R is allowed only as a PARTIAL, and only if a later target reaches >= 2R.
     If the stop needed by rule 1 pushes the best target under 2R -> NO TRADE (never tighten the stop to rescue R:R).
  4. Direction against the daily bias -> label COUNTER-TREND (allowed, must be labelled). Neutral bias -> "no clear bias".
  5. Entry must be between stop and targets; targets on the profit side.

    python3 .claude/skills/risk-rules/scripts/validate_setup.py --instrument NAS100 --direction short \
        --entry 28219.0 --raid-extreme 28239.0 --tp 28159.4 --tp 28108.7 --bias bull
    python3 .claude/skills/risk-rules/scripts/validate_setup.py --setup drafts/setup_N6.json --write

Exit codes: 0 VALID, 1 NO TRADE, 2 usage error.
"""
from __future__ import annotations
import argparse, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
from emconfig import load_config, stop_buffer  # noqa: E402


def bias_label(direction: str, bias: str | None) -> str:
    b = (bias or "").lower()
    if b in ("bull", "bullish", "long"):
        return "with-bias" if direction == "long" else "COUNTER-TREND"
    if b in ("bear", "bearish", "short"):
        return "with-bias" if direction == "short" else "COUNTER-TREND"
    return "no clear bias"


def validate(direction: str, entry: float, raid_extreme: float, targets: list[float], spread: float, wick: float,
             stop: float | None = None, bias: str | None = None, min_rr: float = 2.0, decimals: int = 5) -> dict:
    errors, notes = [], []
    sign = 1 if direction == "short" else -1          # stop side: above for shorts, below for longs
    buffer = spread + wick
    min_stop = round(raid_extreme + sign * buffer, decimals)
    if stop is None:
        stop = min_stop
        notes.append(f"stop set to raid extreme {'+' if sign > 0 else '-'} (spread {spread} + wick {wick}) = {min_stop}")
    elif (stop - min_stop) * sign < -1e-9:
        errors.append(f"stop {stop} is inside the minimum buffer: needs to be at or beyond {min_stop} "
                      f"(raid extreme {raid_extreme} {'+' if sign > 0 else '-'} spread {spread} + wick {wick})")
    if (entry - raid_extreme) * sign > 0:
        errors.append(f"entry {entry} is beyond the raid extreme {raid_extreme}; the raid would already be invalidated")
    risk = round((stop - entry) * sign, decimals + 2)
    if risk <= 0:
        errors.append("stop is on the wrong side of entry")
        risk = float("nan")
    tps = []
    for k, tp in enumerate(targets, 1):
        reward = (entry - tp) * sign
        if reward <= 0:
            errors.append(f"TP{k} {tp} is not on the profit side of entry {entry}")
            tps.append({"name": f"TP{k}", "price": tp, "r": None}); continue
        r = round(reward / risk, 2) if risk == risk else None
        tps.append({"name": f"TP{k}", "price": tp, "r": r})
    best = max([t["r"] for t in tps if t["r"] is not None], default=0)
    if not targets:
        errors.append("no targets given")
    elif best < min_rr:
        errors.append(f"best target is {best}R, under the 1:{min_rr:g} minimum with a rule-compliant stop - NO TRADE")
    for t in tps:
        if t["r"] is not None and t["r"] < min_rr and best >= min_rr:
            t["partial"] = True
            notes.append(f"{t['name']} {t['price']} is {t['r']}R (<{min_rr:g}R): PARTIAL only")
    label = bias_label(direction, bias)
    return {"verdict": "NO TRADE" if errors else "VALID", "direction": direction, "entry": entry, "stop": stop,
            "min_stop": min_stop, "raid_extreme": raid_extreme, "risk_pts": risk, "targets": tps,
            "bias_label": label, "counter_trend": label == "COUNTER-TREND", "errors": errors, "notes": notes}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", help="setup JSON (state/SCHEMA.md); CLI flags override its fields")
    p.add_argument("--write", action="store_true", help="write stop/risk/R/labels back into --setup")
    p.add_argument("--instrument"); p.add_argument("--direction", choices=["long", "short"])
    p.add_argument("--entry", type=float); p.add_argument("--raid-extreme", type=float)
    p.add_argument("--stop", type=float); p.add_argument("--tp", type=float, action="append")
    p.add_argument("--bias"); p.add_argument("--spread", type=float); p.add_argument("--wick", type=float)
    a = p.parse_args(argv)
    s = {}
    if a.setup:
        with open(a.setup) as f:
            s = json.load(f)
    inst = a.instrument or s.get("instrument")
    cfg = load_config()
    if not inst or inst not in cfg["instruments"]:
        print(f"error: instrument required, one of {list(cfg['instruments'])}", file=sys.stderr); return 2
    spec = cfg["instruments"][inst]
    sb = stop_buffer(cfg, inst)
    if spec.get("confluence_only"):
        print(f"error: {inst} is confluence only - no setups on it", file=sys.stderr); return 2
    direction = a.direction or s.get("direction")
    entry = a.entry if a.entry is not None else s.get("entry")
    extreme = a.raid_extreme if a.raid_extreme is not None else (s.get("sweep") or {}).get("extreme")
    tps = a.tp or [t["price"] for t in s.get("targets", [])]
    if None in (direction, entry, extreme):
        print("error: need direction, entry and raid extreme", file=sys.stderr); return 2
    res = validate(direction, entry, extreme, tps,
                   a.spread if a.spread is not None else s.get("spread", sb["spread"]),
                   a.wick if a.wick is not None else s.get("wick", sb["wick_1m"]),
                   a.stop if a.stop is not None else s.get("stop"),
                   a.bias or s.get("daily_bias"), cfg["risk"]["min_rr"], spec["decimals"])
    res["stop_buffer_source"] = ("CLI/setup override" if (a.spread is not None or a.wick is not None or "spread" in s)
                                 else f"config/instruments.json stop_buffer ({sb['status']})")
    if sb["is_default"] and res["stop_buffer_source"].startswith("config"):
        res["notes"].append("stop buffer is a DEFAULT pending Hilda's broker spreads")
    print(json.dumps(res, indent=2))
    if a.write and a.setup:
        merged = [{**orig, **t} for orig, t in zip(s.get("targets", []), res["targets"])] if s.get("targets") else res["targets"]
        s.setdefault("spread", a.spread if a.spread is not None else sb["spread"])
        s.setdefault("wick", a.wick if a.wick is not None else sb["wick_1m"])
        s.update(stop=res["stop"], risk_pts=res["risk_pts"], targets=merged, bias_label=res["bias_label"],
                 validation={"verdict": res["verdict"], "errors": res["errors"], "notes": res["notes"]})
        with open(a.setup, "w") as f:
            json.dump(s, f, indent=2)
    return 0 if res["verdict"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
