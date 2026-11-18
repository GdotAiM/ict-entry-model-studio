import validate_setup as v
import position_size as ps


def test_n6_numbers_reproduce():
    """Shape of a real alert (11 Nov N6): stop = raid extreme + 9, TP1 2.06R, TP2 3.80R, COUNTER-TREND."""
    r = v.validate("short", 28219.0, 28239.0, [28159.4, 28108.7], 1.5, 7.5, bias="bull", decimals=1)
    assert r["verdict"] == "VALID" and r["stop"] == 28248.0 and r["risk_pts"] == 29.0
    assert [t["r"] for t in r["targets"]] == [2.06, 3.8]
    assert r["bias_label"] == "COUNTER-TREND"


def test_long_with_bias():
    r = v.validate("long", 100.0, 95.0, [115.0], 0.5, 0.5, bias="bullish")
    assert r["stop"] == 94.0 and r["risk_pts"] == 6.0 and r["targets"][0]["r"] == 2.5 and r["bias_label"] == "with-bias"


def test_stop_too_tight_rejected():
    r = v.validate("short", 100.0, 105.0, [70.0], 1.0, 2.0, stop=106.0)
    assert r["verdict"] == "NO TRADE" and any("minimum buffer" in e for e in r["errors"])


def test_buffer_pushes_rr_under_2_is_no_trade():
    # Without the buffer: risk 5 -> 2.0R. With spread+wick 3: risk 8 -> 1.25R -> NO TRADE (never tighten the stop).
    r = v.validate("short", 100.0, 105.0, [90.0], 1.0, 2.0)
    assert r["verdict"] == "NO TRADE" and r["targets"][0]["r"] == 1.25


def test_tp1_under_2r_flagged_partial():
    r = v.validate("short", 100.0, 105.0, [92.0, 70.0], 0.0, 0.0)
    assert r["verdict"] == "VALID"
    assert r["targets"][0]["partial"] is True and "partial" not in r["targets"][1]


def test_wrong_side_targets_and_entry():
    r = v.validate("long", 100.0, 95.0, [90.0], 0.5, 0.5)
    assert r["verdict"] == "NO TRADE"
    r = v.validate("short", 106.0, 105.0, [90.0], 0.5, 0.5)
    assert any("beyond the raid extreme" in e for e in r["errors"])


def test_neutral_bias_label():
    assert v.bias_label("long", None) == "no clear bias"


def test_cli_rejects_dxy(capsys):
    assert v.main(["--instrument", "DXY", "--direction", "long", "--entry", "1", "--raid-extreme", "0.9", "--tp", "2"]) == 2


def test_standalone_and_correlated_risk():
    assert ps.risk_pct_for("US100", "short", [])["risk_pct"] == 0.5
    # EUR short + GBP short = same USD-long group -> 0.25% each
    r = ps.risk_pct_for("EURUSD", "short", [("GBPUSD", "short")])
    assert r["mode"] == "correlated" and r["risk_pct"] == 0.25
    # gold is its own group: never stacked with EUR/GBP, whatever the direction
    assert ps.risk_pct_for("XAUUSD", "long", [("EURUSD", "short")])["risk_pct"] == 0.5
    assert ps.risk_pct_for("XAUUSD", "long", [("EURUSD", "long"), ("GBPUSD", "long")])["risk_pct"] == 0.5
    assert ps.correlation_group("CAPITALCOM:GOLD", "short") == "gold-short"
    r = ps.risk_pct_for("XAUUSD", "short", [("XAUUSD", "short", 0.5)])
    assert r["group"] == "gold-short" and r["risk_pct"] == 0.25
    full = [("EURUSD", "long"), ("GBPUSD", "long"), ("EURUSD", "long"), ("GBPUSD", "long")]
    assert ps.risk_pct_for("GBPUSD", "long", full)["allowed"] is False
    # Changed with the group-cap fix: three bare open trades are 0.5 + 0.25 + 0.25 = 1.0% already, so the group is full.
    # (This line used to expect 0.25%, i.e. a 1.25% group reported as inside the 1% cap - the bug.)
    assert ps.risk_pct_for("GBPUSD", "long", full[:3])["allowed"] is False
    three_quarter = [(i, d, 0.25) for i, d in full[:3]]
    assert ps.risk_pct_for("GBPUSD", "long", three_quarter)["risk_pct"] == 0.25


def test_size_formula():
    s = ps.size(0.5, 29.0, equity=10000, value_per_point=1.0)
    assert s["risk_cash"] == 50.0 and s["units"] == round(50 / 29, 4)
    assert "equity x 0.005" in s["formula"]


def test_us_indices_share_one_group_per_direction():
    # US100 + US500 same direction = one correlated group with one 1% cap
    assert ps.correlation_group("US100", "short") == ps.correlation_group("CAPITALCOM:US500", "short") == "US-indices-short"
    r = ps.risk_pct_for("US500", "short", [("US100", "short", 0.5)])
    assert r["mode"] == "correlated" and r["risk_pct"] == 0.25 and r["group_after_pct"] == 0.75
    assert ps.risk_pct_for("US500", "short", [("US100", "short", 0.5), ("US500", "short", 0.25), ("US100", "short", 0.25)])["allowed"] is False
    # opposite directions are not stacked; indices and FX are separate families
    assert ps.risk_pct_for("US500", "long", [("US100", "short", 0.5)])["risk_pct"] == 0.5
    assert ps.risk_pct_for("EURUSD", "long", [("US100", "short", 0.5)])["risk_pct"] == 0.5
    # EURUSD long + GBPUSD long are both short the dollar (= short DXY): one group
    assert ps.risk_pct_for("GBPUSD", "long", [("EURUSD", "long", 0.5)])["group"] == "USD-short"
