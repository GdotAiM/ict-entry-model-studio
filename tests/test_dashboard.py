"""Local dashboard: built from the real state files, EXAMPLE (FABRICATED) data only. No network."""
import json, os
from datetime import datetime
import pytest
import dashboard as db
import riskstate

pytest.importorskip("matplotlib")
NOW = datetime.fromisoformat("2026-01-15T09:40:00+02:00")


@pytest.fixture(scope="module")
def example(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("emdash"))
    now = db.seed_example(root)
    res = db.build(root, os.path.join(root, "dashboard"), now)
    return root, res


def read(root, name):
    return open(os.path.join(root, "dashboard", name)).read()


def test_example_build_writes_every_page_and_labels_example(example):
    root, res = example
    assert res["example"] is True and {"index.html", "lifecycle.html", "recap.html"} <= set(res["pages"])
    assert any(p.startswith("setup-N") for p in res["pages"])
    for p in res["pages"]:
        assert "EXAMPLE DATA" in read(root, p) and "Analysis only, not financial advice" in read(root, p)


def test_dashboard_meters_read_the_real_risk_state(example):
    root, _ = example
    rs = riskstate.risk_state(db.load_setups(root), NOW, db.emconfig.load_config())
    html = read(root, "index.html")
    assert f"realised {rs['daily_pct']:+.2f}% + open risk {rs['open_risk_pct']:.2f}%" in html
    assert f"{rs['drawdown_pct']:.2f}%" in html and "New alerts allowed" in html
    assert "London Open" in html and "NOW 09:40" in html and "(not scanned)" in html


def test_setup_page_has_chart_and_numbered_checklist(example):
    root, res = example
    sid = next(p for p in res["pages"] if p.startswith("setup-N9"))
    html = read(root, sid)
    assert "<svg" in html and "Why it qualified" in html and "11.</span> Data" in html
    assert "Liquidity sweep" in html and "TP1 HIT" in html


def test_recap_from_log_with_unproven_badge(example):
    root, _ = example
    html = read(root, "recap.html")
    assert "UNPROVEN" in html and "No pre-registered test yet" in html and "Week 3" in html
    assert "STOP (gap)" in html and "COUNTER-TREND" in html


def test_lifecycle_counts(example):
    root, _ = example
    html = read(root, "lifecycle.html")
    assert "LOCKED" in html and "CANCELLED" in html and "Recent transitions" in html


def test_locked_state_shows_on_dashboard(tmp_path):
    d = tmp_path / "state" / "setups"; d.mkdir(parents=True)
    for i in range(4):
        t = f"2026-01-15T08:0{i}:00+02:00"
        (d / f"N{i}.json").write_text(json.dumps({"id": f"N{i}", "instrument": "US100", "direction": "short", "risk_pct": 0.5,
                                                  "status": "closed_stop", "targets": [{"name": "TP1", "price": 1, "r": 2}], "alert_time": t,
                                                  "date": "2026-01-15", "entry": 1, "stop": 2, "killzone": "London Open (x)",
                                                  "events": [{"event": "STOP", "time": t, "price": 2, "r": -1.0}]}))
    res = db.build(str(tmp_path), str(tmp_path / "out"), NOW)
    assert res["locked"] is True
    html = open(tmp_path / "out" / "index.html").read()
    assert "LOCKED" in html and "daily loss cap 2% reached" in html
    assert "EXAMPLE DATA" not in html                       # not example data -> no example badge


def test_empty_state_builds(tmp_path):
    res = db.build(str(tmp_path), str(tmp_path / "out"), NOW)
    assert res["pages"] == ["index.html", "lifecycle.html", "recap.html"]
    assert "No setups this trading day yet" in open(tmp_path / "out" / "index.html").read()


def _res(n, r, date="2026-12-10"):
    return [{"date": date, "taken": True, "r": r} for _ in range(n)]


def test_proof_status_rules():
    assert db.proof_status(_res(200, 2.0), None)["badge"] == "UNPROVEN"            # no pre-registration: never proven
    pr = {"id": "EM-1", "registered_at": "2026-12-01", "min_trades": 30, "threshold": 0.0}
    assert db.proof_status(_res(29, 2.0), pr)["badge"] == "UNPROVEN"              # 29 < 30
    assert db.proof_status(_res(100, 3.0, "2026-11-20") + _res(5, 1.0), pr)["n_oos"] == 5   # in-sample ignored
    good = _res(20, 2.0) + _res(15, -1.0)                                           # mean +0.71R, n=35
    assert db.proof_status(good, pr)["badge"] == "MEETS PRE-REGISTERED TEST"
    bad = _res(12, 2.0) + _res(23, -1.0)                                            # mean +0.03R, CI low < 0
    assert db.proof_status(bad, pr)["badge"] == "FAILS PRE-REGISTERED TEST"


def test_watch_saves_closed_bars_for_the_dashboard(tmp_path):
    import test_watch as tw
    import shutil
    from conftest import ROOT
    b = tmp_path / "bars"; b.mkdir()
    for iv in ("1m", "1d"):
        shutil.copy(os.path.join(ROOT, "examples", f"EXAMPLE_bars_US100_{iv}.csv"), b / f"US100_{iv}.csv")
    tw.cycle(tmp_path, "09:27", dry_run=True)
    rows, meta = db.load_bars(str(tmp_path), "US100")
    assert rows and rows[-1]["time"] == "2026-01-15T09:26:00+02:00"              # closed bars only
    assert meta["saved_at"].startswith("2026-01-15T09:27")


def test_watchlist_has_all_six_with_a_bias_each(example):
    root, _ = example
    html = read(root, "index.html")
    wl = html[html.index("Watchlist &amp; bias"):html.index("<h2>Setups")]
    order = [wl.index(f"<b>{i}</b>") for i in ("US100", "US500", "EURUSD", "GBPUSD", "XAUUSD", "DXY")]
    assert order == sorted(order) and "no bias read yet" not in wl         # every row has a bias, DXY included
    assert "Switched off" not in html and "CAPITALCOM:GOLD" in wl and "group gold" in wl
    assert "context only: no setups, no alerts" in wl and wl.count("DXY bull confirms (inverse)") == 3
    assert "CAPITALCOM:US500" in wl and "TVC:DXY" in wl and "group US-indices" in wl


def test_example_us500_setup_is_correlated_with_us100(example):
    root, _ = example
    s = json.load(open(os.path.join(root, "state", "setups", "S3.json")))
    n = json.load(open(os.path.join(root, "state", "setups", "N9.json")))
    assert s["instrument"] == "US500" and s["symbol"] == "CAPITALCOM:US500" and n["risk_pct"] == 0.5
    assert s["risk_mode"] == "correlated" and s["risk_pct"] == 0.25 and "US-indices-short" in s["caveats"][0]
    assert "US-indices-short group" in read(root, "index.html")
    assert not any(f.startswith("DXY") for f in os.listdir(os.path.join(root, "state", "setups")))
