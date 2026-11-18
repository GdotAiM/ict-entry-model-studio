import json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for sub in ("lib", "scripts", "examples", *[os.path.join(".claude", "skills", s, "scripts") for s in
            ("killzones", "market-data", "ict-bias", "liquidity", "setup-detection", "risk-rules", "alert-format", "chart-markup")]):
    sys.path.insert(0, os.path.join(ROOT, sub))

import pytest  # noqa: E402

EXAMPLE_BARS = os.path.join(ROOT, "examples", "EXAMPLE_bars_US100_1m.csv")
EXAMPLE_SETUP = os.path.join(ROOT, "examples", "EXAMPLE_setup_EX1.json")


def bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


@pytest.fixture
def example_bars():
    import detect
    return detect.load_csv(EXAMPLE_BARS)


@pytest.fixture
def example_setup():
    with open(EXAMPLE_SETUP) as f:
        return json.load(f)


@pytest.fixture
def short_setup():
    """Minimal pending short, FABRICATED numbers."""
    return {"id": "T1", "instrument": "US100", "direction": "short", "entry": 100.0, "stop": 110.0,
            "sweep": {"extreme": 105.0}, "targets": [{"name": "TP1", "price": 80.0}, {"name": "TP2", "price": 70.0}],
            "status": "pending", "alert_time": "2026-01-15T09:00:00+02:00", "events": []}
