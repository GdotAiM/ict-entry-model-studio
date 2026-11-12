#!/usr/bin/env python3
"""Trade recap from the alert log: closed setups, R per setup, by model/session/instrument/bias label.

A setup's R = sum of its TP partial R (weighted by --partial-share for TP1) and the R of its closing event.
Default convention (matches the live alerts): TP1 banks `partial_share` of the position at TP1's R;
the rest closes at TP2 / stop / BE. With one target, TP1 closes the whole trade.

    python3 scripts/recap.py [--log state/alerts_log.csv] [--from 2026-11-09] [--to 2026-11-13] [--partial-share 0.5]
"""
from __future__ import annotations
import argparse, csv, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_log import n_targets  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLOSE = {"TP2", "TP3", "STOP", "BE"}
ZERO = {"VOID", "INVALID", "CANCELLED"}


def setup_results(rows: list[dict], partial_share: float = 0.5) -> list[dict]:
    by = defaultdict(list)
    for r in rows:
        by[(r["date"], r["setup_id"])].append(r)
    out = []
    for (d, sid), evs in by.items():
        kinds = {e["event"]: e for e in evs}
        meta = evs[0]
        res = {"date": d, "setup_id": sid, "instrument": meta["instrument"], "session": meta["session"],
               "direction": meta["direction"], "label": meta.get("label") or "unlabelled", "model": meta.get("model") or ""}
        single_target = "TP1" in kinds and n_targets(kinds["TP1"]) <= 1
        if kinds.keys() & ZERO and "FILLED" not in kinds:
            res.update(outcome=sorted(kinds.keys() & ZERO)[0], r=0.0, taken=False)
        elif kinds.keys() & CLOSE or single_target:
            closing = next((kinds[k] for k in ("TP3", "TP2", "STOP", "BE") if k in kinds), kinds.get("TP1"))
            r_close = float(closing["r"] or 0)
            if "TP1" in kinds and closing["event"] != "TP1":
                r = partial_share * float(kinds["TP1"]["r"] or 0) + (1 - partial_share) * r_close
            else:
                r = r_close
            res.update(outcome=closing["event"], r=round(r, 2), taken=True)
        else:
            res.update(outcome="OPEN", r=None, taken="FILLED" in kinds)
        out.append(res)
    return sorted(out, key=lambda x: (x["date"], x["setup_id"]))


def summarise(results: list[dict], key: str) -> dict:
    agg = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "total_r": 0.0})
    for r in results:
        if not r["taken"] or r["r"] is None:
            continue
        a = agg[str(r[key])]
        a["trades"] += 1; a["total_r"] = round(a["total_r"] + r["r"], 2)
        a["wins"] += r["r"] > 0; a["losses"] += r["r"] < 0
    return dict(agg)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default=os.path.join(ROOT, "state", "alerts_log.csv"))
    p.add_argument("--from", dest="frm"); p.add_argument("--to"); p.add_argument("--partial-share", type=float, default=0.5)
    a = p.parse_args(argv)
    if not os.path.exists(a.log):
        print("no alert log yet", file=sys.stderr); return 1
    with open(a.log, newline="") as f:
        rows = [r for r in csv.DictReader(f) if (not a.frm or r["date"] >= a.frm) and (not a.to or r["date"] <= a.to)]
    res = setup_results(rows, a.partial_share)
    print(json.dumps({"setups": res, "by_instrument": summarise(res, "instrument"), "by_session": summarise(res, "session"),
                      "by_label": summarise(res, "label"), "by_model": summarise(res, "model"),
                      "total_r": round(sum(r["r"] for r in res if r["taken"] and r["r"] is not None), 2)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
