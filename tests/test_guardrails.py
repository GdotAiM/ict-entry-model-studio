"""Analysis only: the repo must never contain broker / order-placement code."""
import os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FORBIDDEN = [r"MetaTrader5", r"\bmt5\.", r"oandapyV20", r"v20\.Context", r"ib_insync", r"ccxt", r"alpaca_trade_api",
             r"\bplace_order\s*\(", r"\bcreate_order\s*\(", r"\bsubmit_order\s*\(", r"orders/market", r"/v3/accounts/"]


def _py_files():
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in (".git", "__pycache__", ".venv", "venv")]
        for f in files:
            if f.endswith(".py") and f != "test_guardrails.py":
                yield os.path.join(d, f)


def test_no_broker_or_order_code():
    hits = []
    for p in _py_files():
        txt = open(p, encoding="utf-8").read()
        hits += [f"{os.path.relpath(p, ROOT)}: {pat}" for pat in FORBIDDEN if re.search(pat, txt)]
    assert hits == []


def test_examples_are_labelled():
    ex = os.path.join(ROOT, "examples")
    for f in os.listdir(ex):
        if f.endswith((".csv", ".json", ".md", ".png")) and f != "README.md":
            assert f.startswith("EXAMPLE_"), f
    assert "FABRICATED" in open(os.path.join(ex, "README.md")).read()


def test_tradingview_bridge_is_read_only_candles():
    src = open(os.path.join(ROOT, "tools", "tradingview-bridge", "bridge.mjs"), encoding="utf-8").read()
    imports = re.findall(r"^import .*$", src, re.M)
    assert imports == ["import { getCandles } from '@mathieuc/tradingview/data';"]
    for bad in (r"credentials", r"sessionid", r"signature", r"\.env", r"fetch\(", r"/backtest", r"order"):
        assert not re.search(bad, src, re.I), bad
