import models as md
import detect


def _setup(example_bars):
    c = detect.find_setup(example_bars, "short", [20030.0])[0]
    return c, c["mss"]


def test_breaker_and_unicorn(example_bars):
    c, m = _setup(example_bars)
    bb = md.breaker_block(example_bars, m, "short")
    assert bb["time"].endswith("09:15:00+02:00") and (bb["low"], bb["high"]) == (20014.0, 20021.0)
    u = md.unicorn(example_bars, m, "short", c["ote"])
    assert u["overlap"] == {"low": 20014.0, "high": 20021.0} and u["entry"] == 20021.0
    assert u["fvg"]["low"] == 20007.0


def test_unicorn_requires_ote_touch(example_bars):
    c, m = _setup(example_bars)
    far = {"low": 20035.0, "sweet": 20036.0, "high": 20040.0}
    assert md.unicorn(example_bars, m, "short", far) is None


def test_no_breaker_without_broken_mss():
    assert md.breaker_block([], {"broken": False}, "short") is None


FULL = {"instrument": "EURUSD", "direction": "short", "entry_time": "2026-11-11T08:20:00-05:00",  # Wed 08:20 NY
        "entry_tf": "1m", "sweep_tf": "1m", "sweep_label": "PDH", "mss_valid": True, "fvg_side_of_eq": "premium",
        "in_ote": True, "unicorn": False, "zone_pct": 70.0, "daily_took_20d": "low", "weekly_bias": "bear",
        "daily_bias": "bear", "bias_confidence": 0.8, "tp1_dist": 0.0020, "pip": 0.0001, "london_direction": "up",
        "target_is_old_daily": True, "weekly_expansion": True, "liquidity_unswept": True}


def _by(ch):
    return {c["code"]: c for c in ch}


def test_charter_full_context_fx_short():
    ch = _by(md.charter_models(FULL))
    assert ch["M13"]["status"] == "detected"
    assert ch["M12"]["status"] == "detected"           # PDH raid, 1m, 20 pips
    assert ch["M1"]["status"] == "detected"            # NY 07-11, Wed, premium, against London's up move, 20 pips
    assert ch["M7"]["status"] == "detected" and "M6" not in ch
    assert ch["M3"]["status"] == ch["M4"]["status"] == "not mechanical"
    assert ch["M5"]["status"] in ("not detected", "partial")   # 1m entry, 20-pip target: not a 40-50 pip 15m model


def test_charter_friday_and_outside_window():
    ctx = {**FULL, "entry_time": "2026-11-13T03:00:00-05:00"}  # Friday London
    ch = _by(md.charter_models(ctx))
    assert ch["M1"]["checks"]["Mon-Wed, or Thu with liquidity unswept, never Fri"] is False
    assert ch["M1"]["status"] != "detected"
    assert ch["M13"]["checks"]["canonical window (FX 07-10 NY, indices 08:30-11 NY)"] is None  # London use: skipped, not failed


def test_pip_targets_not_applied_to_indices():
    ctx = {**FULL, "instrument": "NAS100", "tp1_dist": 60.0, "pip": 1.0}
    ch = _by(md.charter_models(ctx))
    assert ch["M12"]["checks"]["target ~20 pips (FX only)"] is None
    assert ch["M1"]["checks"]["target 15-20 pips (FX only)"] is None


def test_sparse_context_is_honest():
    ch = _by(md.charter_models({"direction": "long"}))
    assert all(c["status"] in ("not mechanical", "insufficient data") for c in ch.values())


def test_names_and_stages():
    ch = md.charter_models(FULL)
    n = md.name_models(None, ch)
    assert n["model"].startswith("ICT 2022 model") and n["charter_model"].startswith("M13 2022 model (detected)")
    assert md.name_models({"x": 1}, ch)["model"].startswith("Unicorn")
    assert md.charter_stage("pending") == "Trade Planning" and md.charter_stage("filled") == "Trade Execution"
    assert md.charter_stage("tp1") == "Trade Management" and md.charter_stage("closed_stop").endswith("(closed)")
    assert md.charter_stage("void").endswith("(cancelled)")
