import emconfig
import validate_setup as v
import render_alert as ra
import check_alert as ca


def validated(s):
    sb = emconfig.stop_buffer(emconfig.load_config(local=None), s["instrument"])
    s.setdefault("spread", sb["spread"]); s.setdefault("wick", sb["wick_1m"])
    r = v.validate(s["direction"], s["entry"], s["sweep"]["extreme"], [t["price"] for t in s["targets"]],
                   s["spread"], s["wick"], None, s["daily_bias"], 2.0, s["decimals"])
    s.update(stop=r["stop"], risk_pts=r["risk_pts"],
             targets=[{**t, **{k: x[k] for k in x if k != "name"}} for t, x in zip(s["targets"], r["targets"])],
             bias_label=r["bias_label"], validation={"verdict": r["verdict"]})
    return s


def test_new_alert_has_every_field_and_passes(example_setup):
    s = validated(example_setup)
    txt = ra.render(s, "NEW")
    for k in ca.REQUIRED_NEW + ["Analysis only, not financial advice", "sell limit 20026.0", "2.24R", "3.44R",
                                "skip if TP1", "drop if 20042.0 breaks first", "cancel if unfilled by 12:00 SAST"]:
        assert k in txt, k
    assert ca.check(txt, "NEW", s) == []


def test_counter_trend_must_be_labelled(example_setup):
    s = validated({**example_setup, "daily_bias": "bull"})
    txt = ra.render(s, "NEW")
    assert "COUNTER-TREND" in txt.splitlines()[0]
    assert any("COUNTER-TREND" in e for e in ca.check(txt.replace("COUNTER-TREND", "x"), "NEW", s))


def test_fallback_disclosed(example_setup):
    s = validated({**example_setup, "fallback": True, "data_source": "yfinance NQ=F"})
    txt = ra.render(s, "NEW")
    assert "yfinance FALLBACK" in txt and ca.check(txt, "NEW", s) == []
    assert any("does not disclose" in e for e in ca.check(txt.replace("FALLBACK", ""), "NEW", s))


def test_partial_flag_required(example_setup):
    s = validated({**example_setup, "targets": [{"name": "TP1", "price": 20000.0}, {"name": "TP2", "price": 19940.0}]})
    txt = ra.render(s, "NEW")
    assert "PARTIAL only" in txt and ca.check(txt, "NEW", s) == []
    assert any("PARTIAL" in e for e in ca.check(txt.replace("PARTIAL only", ""), "NEW", s))


def test_unvalidated_setup_blocked(example_setup):
    s = validated(example_setup); s["validation"]["verdict"] = "NO TRADE"
    assert any("verdict" in e for e in ca.check(ra.render(s, "NEW"), "NEW", s))


def test_status_alerts(example_setup):
    s = validated(example_setup)
    s["events"] = [{"event": "TP1", "r": 2.24}]
    for ev in ("FILLED", "TP1", "TP2", "STOP", "BE", "VOID", "INVALID", "CANCELLED"):
        txt = ra.render(s, ev, "09:35", 19970.0)
        assert ca.check(txt, ev, s) == [], ev
        assert "Charter stage:" in txt and txt.rstrip().endswith("Nothing here places or manages orders.")
    assert "(+2.24R)" in ra.render(s, "TP1", "09:35", 19970.0)


def test_banned_language_and_missing_source():
    bad = "I placed the order. Guaranteed win. Charter stage: x Data: UNKNOWN SOURCE Analysis only, not financial advice"
    errs = ca.check(bad, "FILLED")
    assert any("banned" in e for e in errs) and any("no data source" in e for e in errs)


def test_caveats_never_render_dot_semicolon(example_setup):
    s = validated(example_setup)
    s["caveats"] = ["2 correlated trades in US-indices-short: new one 0.25% (group 0.75% of max 1%).", "stop buffer is a DEFAULT"]
    s["confluence"] = ["DXY bull confirms (inverse).", "draw on liquidity PDL"]
    txt = ra.render(s, "NEW")
    assert ".;" not in txt and "max 1%); stop buffer is a DEFAULT; skip if TP1" in txt
    assert "Confluence: DXY bull confirms (inverse); draw on liquidity PDL" in txt
    assert ca.check(txt, "NEW", s) == []
