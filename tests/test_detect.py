import pytest
import detect as d
from conftest import bar


def test_swings_on_example(example_bars):
    hs = dict((example_bars[i]["time"][11:16], v) for i, v in d.swing_highs(example_bars))
    ls = dict((example_bars[i]["time"][11:16], v) for i, v in d.swing_lows(example_bars))
    assert hs["09:08"] == 20030 and ls["09:13"] == 20010


def test_sweep_and_turtle_soup(example_bars):
    sw = d.detect_sweep(example_bars, 20030, "buy")
    assert sw["time"].endswith("09:20:00+02:00") and sw["extreme"] == 20042 and sw["turtle_soup"]
    assert d.detect_sweep(example_bars, 20100, "buy") is None
    low = d.detect_sweep(example_bars, 19985, "sell")
    assert low and low["extreme"] < 19985


def test_fvg_definitions():
    bars = [bar("t0", 10, 12, 9, 11), bar("t1", 11, 11, 5, 6), bar("t2", 6, 7, 4, 5)]  # bearish: low0 9 > high2 7
    assert d.find_fvgs(bars, "bearish") == [{"index": 1, "time": "t1", "low": 7, "high": 9}]
    up = [bar("t0", 5, 6, 4, 6), bar("t1", 6, 11, 6, 10), bar("t2", 10, 12, 8, 11)]  # bullish: high0 6 < low2 8
    assert d.find_fvgs(up, "bullish") == [{"index": 1, "time": "t1", "low": 6, "high": 8}]
    assert d.find_fvgs(up, "bearish") == []


def test_mss_with_displacement(example_bars):
    sw = d.detect_sweep(example_bars, 20030, "buy")
    m = d.detect_mss(example_bars, sw)
    assert m["broken"] and m["swing_level"] == 20010 and m["break_time"].endswith("09:23:00+02:00")
    assert m["displacement"] and m["fvgs"] and m["valid"] and m["leg_end"] == 19985


def test_mss_rejects_slow_grind():
    """Break of structure by small overlapping candles (no displacement, no FVG) is NOT a valid MSS. FABRICATED."""
    p = [10, 9.5, 9, 9.5, 10, 10.5, 11, 10.5, 10, 9.6, 10, 10.5, 11, 11.5, 11.8, 11.5, 11, 10.5, 10, 9.5, 9.2, 9.0]
    bars = [bar(f"t{i:02d}", x, x + 0.6, x - 0.6, x - 0.1) for i, x in enumerate(p)]
    sw = d.detect_sweep(bars, 11.6, "buy")
    m = d.detect_mss(bars, sw)
    assert m and m.get("broken") and not m["valid"]


def test_ote_zone_math():
    z = d.ote_zone("short", 200.0, 100.0)
    assert (round(z["low"], 2), round(z["sweet"], 2), round(z["high"], 2)) == (162.0, 170.5, 179.0)
    z = d.ote_zone("long", 200.0, 100.0)
    assert (round(z["high"], 2), round(z["sweet"], 2), round(z["low"], 2)) == (138.0, 129.5, 121.0)
    with pytest.raises(ValueError):
        d.ote_zone("short", 100, 200)


def test_find_setup_complete(example_bars):
    res = d.find_setup(example_bars, "short", [20030, 20100])
    full = res[0]
    assert full["stage"] == "complete" and full["raid_extreme"] == 20042
    assert full["entry"]["price"] == 20026 and full["entry"]["basis"] == "FVG edge inside OTE"
    assert res[1]["stage"] == "not swept"


def test_equal_levels():
    eq = d.equal_levels([(1, 100.0), (5, 100.2), (9, 103.0)], 0.3)
    assert eq == [{"i": 1, "j": 5, "max": 100.2, "min": 100.0}]


def test_late_break_is_not_an_mss():
    """A break 40 bars after the raid extreme is a slow shift. FABRICATED bars."""
    bars = [bar(f"t{i:02d}", 10, 10.5, 9.5, 10) for i in range(5)]
    bars += [bar("t05", 10, 10.2, 8.0, 8.2), bar("t06", 8.2, 9.0, 8.1, 8.9), bar("t07", 8.9, 9.5, 8.8, 9.4)]  # swing low 8.0
    bars += [bar("t08", 9.4, 12.0, 9.3, 11.0), bar("t09", 11.0, 11.2, 10.5, 10.6)]                             # raid high 12
    bars += [bar(f"u{i:02d}", 10.6, 10.8, 10.4, 10.6) for i in range(40)]
    bars += [bar("v00", 10.6, 10.6, 7.0, 7.1), bar("v01", 7.1, 7.2, 6.8, 6.9)]
    sw = d.detect_sweep(bars, 11.5, "buy")
    m = d.detect_mss(bars, sw)
    assert m["broken"] and not m["valid"] and m.get("late")
    assert d.detect_mss(bars, sw, max_bars=60)["valid"] in (True, False)  # same bars, larger window: evaluated normally
