import os
from datetime import date, timedelta
import pytest
import mtf_bias as mb
from conftest import ROOT

EX_DAILY = os.path.join(ROOT, "examples", "EXAMPLE_bars_NAS100_1d.csv")


def trend(n=300, step=-5.0, start=21000.0):
    """FABRICATED daily bars: steady staircase (two up days, two down days) drifting by `step`."""
    out, d, px = [], date(2025, 1, 1), start
    while len(out) < n:
        if d.weekday() < 5:
            k = len(out)
            px += step + (8 if k % 4 in (1, 2) else -8) * (1 if step < 0 else -1) * -1
            out.append({"time": d.isoformat(), "open": px + 2, "high": px + 15, "low": px - 15, "close": px - 2 if step < 0 else px + 4})
        d += timedelta(days=1)
    return out


def test_aggregate_weeks_and_months():
    d = trend(30)
    w, m = mb.aggregate(d, "W"), mb.aggregate(d, "M")
    assert sum(1 for _ in w) >= 6 and len(m) == 2
    assert m[0]["high"] == max(b["high"] for b in d if b["time"].startswith("2025-01"))


def test_bear_example_reads_strong_bear_with_reasons():
    r = mb.mtf_bias(mb.load_csv(EX_DAILY))
    assert r["bias"] == "bear" and r["strength"] == "strong" and r["confidence_label"] == "high"
    assert any(x.startswith("monthly:") for x in r["reasoning"]) and any("weighted score" in x for x in r["reasoning"])
    assert r["invalidation"]["rule"].startswith("bias wrong on a 1H close above")
    assert r["draw"]["name"] in ("PWL", "old low 2026-01-09") and r["draw"]["level"] == 19930.0


def test_extended_penalty_and_location_bonus():
    d = mb.load_csv(EX_DAILY)
    deep = mb.mtf_bias(d)                          # last close near the range low -> EXTENDED
    assert deep["dealing_range"]["extended"] and any(x.startswith("EXTENDED") for x in deep["reasoning"])
    hi = mb.mtf_bias(d, last=deep["dealing_range"]["high"] - 1)   # same structure, priced in premium -> good location
    assert not hi["dealing_range"]["extended"] and hi["confidence"] > deep["confidence"]


def test_bull_mirror_and_dxy_disagreement():
    d = mb.load_csv(EX_DAILY)
    flip = [{"time": b["time"], "open": -b["open"], "high": -b["low"], "low": -b["high"], "close": -b["close"]} for b in d]
    up = mb.mtf_bias(flip)
    assert up["bias"] == "bull" and up["invalidation"]["rule"].endswith("below")
    # an instrument quoted vs USD that is bear while DXY is also bear -> disagreement lowers confidence
    alone = mb.mtf_bias(d)
    both = mb.mtf_bias(d, dxy=d, inverse_to_dxy=True)
    assert both["confidence"] < alone["confidence"] and any("DISAGREES" in x for x in both["reasoning"])
    conf = mb.mtf_bias(d, dxy=flip, inverse_to_dxy=True)
    assert any("confirms" in x for x in conf["reasoning"])


def test_taken_pools_are_not_draws():
    d = mb.load_csv(EX_DAILY)
    dr = mb.draws(d, mb.aggregate(d, "W"), mb.aggregate(d, "M"), d[-1]["close"])
    assert all(p["level"] < d[-1]["close"] for p in dr["below"])
    lows_after = {p["name"] for p in dr["below"]}
    assert "PDL" not in lows_after  # the forming bar (14 Jan here) traded below the 13 Jan low


def test_needs_history():
    with pytest.raises(ValueError):
        mb.mtf_bias(trend(30))
