#!/usr/bin/env python3
"""Gate for alerts: every required field present, labels and disclosures correct, no trade-execution or hype language.

    python3 .claude/skills/alert-format/scripts/check_alert.py --file alerts/2026-11-11_N6_NEW.md --event NEW \
        [--setup state/setups/N6.json]
Exit codes: 0 PASS, 1 FAIL (do not send), 2 usage error.
"""
from __future__ import annotations
import argparse, json, re, sys

REQUIRED_NEW = ["Timeframe:", "Kill zone:", "Model:", "Charter stage:", "Sweep:", "MSS:", "Entry:", "Stop:", "Risk:",
                "Targets:", "Size:", "Confluence:", "Caveats:", "Data:"]
REQUIRED_ALL = ["Charter stage:", "Data:", "Analysis only, not financial advice"]
BANNED = [r"\bguarantee", r"risk[- ]free", r"can'?t lose", r"sure thing", r"easy money", r"\b100% win",
          r"\bI (?:have )?(?:placed|opened|closed|executed|entered)\b", r"\border (?:has been )?(?:placed|sent|executed)\b",
          r"\bbuy now\b", r"\bsell now\b", r"\bbet the farm\b", r"\ball[- ]in\b"]


def check(text: str, event: str, setup: dict | None = None) -> list[str]:
    errs = []
    for k in REQUIRED_ALL + (REQUIRED_NEW if event == "NEW" else []):
        if k not in text:
            errs.append(f"missing field: {k!r}")
    for pat in BANNED:
        if re.search(pat, text, re.I):
            errs.append(f"banned language: /{pat}/")
    if "UNKNOWN SOURCE" in text:
        errs.append("no data source - never send prices without a source")
    if re.search(r"yfinance", text, re.I) and "FALLBACK" not in text:
        errs.append("yfinance mentioned without the word FALLBACK")
    if setup:
        if setup.get("fallback") and "FALLBACK" not in text:
            errs.append("setup used yfinance fallback data but the alert does not disclose it")
        if setup.get("bias_label") == "COUNTER-TREND" and "COUNTER-TREND" not in text:
            errs.append("counter-trend setup not labelled COUNTER-TREND")
        if event == "NEW":
            v = (setup.get("validation") or {}).get("verdict")
            if v != "VALID":
                errs.append(f"setup validation verdict is {v!r}, not VALID - run validate_setup.py --write")
            for t in setup.get("targets", []):
                if t.get("r") is not None and t["r"] < 2 and "PARTIAL" not in text:
                    errs.append(f"{t['name']} is {t['r']}R (<2R) but not flagged PARTIAL")
    return errs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True); p.add_argument("--event", required=True); p.add_argument("--setup")
    a = p.parse_args(argv)
    try:
        text = open(a.file).read()
        setup = json.load(open(a.setup)) if a.setup else None
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 2
    errs = check(text, a.event.upper(), setup)
    if errs:
        print("FAIL\n- " + "\n- ".join(errs)); return 1
    print("PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
