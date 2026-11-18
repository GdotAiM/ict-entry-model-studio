import json
import pytest
import fetch_bars as fb

ROWS = [{"time": "2026-01-15T09:00:00+02:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]  # FABRICATED


def boom(*a):
    raise RuntimeError("timeout")


def test_tradingview_primary():
    rows, meta = fb.fetch("US100", "1m", 10, tv_fetch=lambda *a: ROWS, yf_fetch=boom)
    assert rows == ROWS and meta["fallback"] is False
    assert meta["source"].startswith("TradingView CAPITALCOM:US100")


def test_resolve_by_tv_symbol():
    rows, meta = fb.fetch("CAPITALCOM:GOLD", "5m", 10, tv_fetch=lambda *a: ROWS, yf_fetch=boom)
    assert meta["instrument"] == "XAUUSD"


def test_yfinance_fallback_is_flagged():
    seen = {}
    def yf(sym, iv, n):
        seen["sym"] = sym; return ROWS
    rows, meta = fb.fetch("DXY", "1m", 10, tv_fetch=boom, yf_fetch=yf)
    assert meta["fallback"] is True and "FALLBACK" in meta["source"] and seen["sym"] == "DX-Y.NYB"
    assert "timeout" in meta["fallback_reason"]


def test_empty_tv_counts_as_failure():
    _, meta = fb.fetch("EURUSD", "1m", 10, tv_fetch=lambda *a: [], yf_fetch=lambda *a: ROWS)
    assert meta["fallback"] is True


def test_no_data_never_invents():
    with pytest.raises(LookupError, match="NO DATA"):
        fb.fetch("GBPUSD", "1m", 10, tv_fetch=boom, yf_fetch=lambda *a: [])


def test_unknown_instrument_and_interval():
    with pytest.raises(KeyError):
        fb.fetch("BTCUSD", "1m", 10, tv_fetch=lambda *a: ROWS)
    with pytest.raises(ValueError):
        fb.fetch("US100", "2m", 10, tv_fetch=lambda *a: ROWS)


def test_write_csv_and_meta(tmp_path):
    rows, meta = fb.fetch("US100", "1m", 10, tv_fetch=lambda *a: ROWS)
    out = tmp_path / "b" / "x.csv"
    fb.write_csv(rows, str(out), meta)
    assert out.read_text().splitlines()[0] == "time,open,high,low,close"
    assert json.loads((tmp_path / "b" / "x.csv.meta.json").read_text())["rows"] == 1


def test_4h_resample_aligned_to_ny_session():
    pd = pytest.importorskip("pandas")
    idx = pd.date_range("2026-01-15 22:00", periods=30, freq="1h", tz="UTC")     # FABRICATED flat bars
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)
    out = fb._resample_4h(df).tz_convert("America/New_York")
    assert {t.hour for t in out.index} <= {1, 5, 9, 13, 17, 21}                  # 17:00 NY session grid, not UTC midnight
    assert 9 in {t.hour for t in out.index}


def test_tradingview_naive_timestamps_use_local_zone_rules_for_that_date(monkeypatch):
    """A machine in New York: Jan bar 09:00 local = 16:00 SAST (EST); Jul bar 09:00 local = 15:00 SAST (EDT)."""
    import sys, time, types
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"open": [1.0, 1.0], "high": [2.0, 2.0], "low": [0.5, 0.5], "close": [1.5, 1.5]},
                      index=pd.to_datetime(["2026-01-15 09:00", "2026-07-15 09:00"]))
    mod = types.ModuleType("tvDatafeed")
    class Interval:
        in_1_minute = in_5_minute = in_15_minute = in_1_hour = in_4_hour = in_daily = 0
    class TvDatafeed:
        def get_hist(self, *a, **k): return df
    mod.Interval, mod.TvDatafeed = Interval, TvDatafeed
    monkeypatch.setitem(sys.modules, "tvDatafeed", mod)
    monkeypatch.setenv("TZ", "America/New_York"); time.tzset()
    try:
        rows = fb._tvdatafeed_fetch("CAPITALCOM:US100", "1m", 2)
    finally:
        monkeypatch.undo(); time.tzset()
    assert [r["time"][11:16] for r in rows] == ["16:00", "15:00"]


# ---------------------------------------------------------------- TradingView-API (Node bridge) backend
import subprocess, types, os as _os


def _bridge(tmp_path):
    d = tmp_path / "bridge"
    (d / "node_modules" / "@mathieuc" / "tradingview").mkdir(parents=True, exist_ok=True)
    return str(d)


def _runner(stdout="", stderr="", code=0, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append(cmd)
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)
    return run


# FABRICATED bars. 1767254400 = 2026-01-01T08:00:00Z = 10:00 SAST
C1 = '[{"time":1767254460,"open":2,"high":3,"low":1,"close":2.5,"volume":9},{"time":1767254400,"open":1,"high":2,"low":0.5,"close":1.5,"volume":7}]'


def test_tvapi_converts_to_sast_sorts_and_maps_timeframe(tmp_path):
    seen = []
    rows = fb._tvapi_fetch("CAPITALCOM:US100", "1m", 2, run=_runner(C1, seen=seen), bridge_dir=_bridge(tmp_path))
    assert [r["time"] for r in rows] == ["2026-01-01T10:00:00+02:00", "2026-01-01T10:01:00+02:00"]
    assert rows[0]["close"] == 1.5 and set(rows[0]) == {"time", "open", "high", "low", "close"}
    assert seen[0][-3:] == ["CAPITALCOM:US100", "1", "2"]
    fb._tvapi_fetch("CAPITALCOM:US100", "4h", 2, run=_runner(C1, seen=seen), bridge_dir=_bridge(tmp_path))
    assert seen[1][-2] == "240"


@pytest.mark.parametrize("kw,msg", [
    (dict(code=1, stderr="CONNECTION_ERROR: ENOTFOUND"), "CONNECTION_ERROR"),
    (dict(stdout="not json"), "unparseable"),
    (dict(stdout="[]"), "no bars"),
    (dict(stdout='[{"time":1}]'), "unparseable"),
])
def test_tvapi_failures_raise_runtimeerror(tmp_path, kw, msg):
    with pytest.raises(RuntimeError, match=msg):
        fb._tvapi_fetch("CAPITALCOM:US100", "1m", 2, run=_runner(**kw), bridge_dir=_bridge(tmp_path))


def test_tvapi_not_installed_or_no_node(tmp_path):
    with pytest.raises(RuntimeError, match="not installed"):
        fb._tvapi_fetch("X:Y", "1m", 1, run=_runner(C1), bridge_dir=str(tmp_path / "nope"))
    def nonode(*a, **k): raise FileNotFoundError
    with pytest.raises(RuntimeError, match="node not found"):
        fb._tvapi_fetch("X:Y", "1m", 1, run=nonode, bridge_dir=_bridge(tmp_path))
    def slow(*a, **k): raise subprocess.TimeoutExpired("node", 90)
    with pytest.raises(RuntimeError, match="timed out"):
        fb._tvapi_fetch("X:Y", "1m", 1, run=slow, bridge_dir=_bridge(tmp_path))


def test_tvapi_daily_alignment_guard(tmp_path):
    # 2026-01-14T22:00Z = 2026-01-15 00:00 SAST (OANDA 17:00 NY open in EST) -> accepted, labelled as the trading day
    ok = '[{"time":1768428000,"open":1,"high":2,"low":0.5,"close":1.5,"volume":1}]'
    rows = fb._tvapi_fetch("CAPITALCOM:US100", "1d", 1, run=_runner(ok), bridge_dir=_bridge(tmp_path))
    assert fb.label_daily(rows)[0]["time"] == "2026-01-15"
    # a midday-aligned daily bar would be mis-dated by label_daily -> refused
    bad = '[{"time":1768467600,"open":1,"high":2,"low":0.5,"close":1.5,"volume":1}]'
    with pytest.raises(RuntimeError, match="refusing to mis-date"):
        fb._tvapi_fetch("CAPITALCOM:US100", "1d", 1, run=_runner(bad), bridge_dir=_bridge(tmp_path))


def test_dxy_daily_opens_at_midnight_utc(tmp_path):
    # TVC:DXY daily bars open 00:00 UTC = 02:00 SAST (seen on a real fetch, 18 Nov 2026). Config allows hour 2 for DXY
    # only; label_daily's +2h shift still gives the right date. The same bar on US100 is refused.
    assert fb.daily_open_hours("TVC:DXY") == (2, 23, 0)
    assert fb.daily_open_hours("CAPITALCOM:US100") == (23, 0, 1)
    dxy = '[{"time":1768435200,"open":99,"high":99.5,"low":98.8,"close":99.2,"volume":0}]'   # 2026-01-15 00:00Z
    rows = fb._tvapi_fetch("TVC:DXY", "1d", 1, run=_runner(dxy), bridge_dir=_bridge(tmp_path))
    assert fb.label_daily(rows)[0]["time"] == "2026-01-15"
    with pytest.raises(RuntimeError, match="refusing to mis-date"):
        fb._tvapi_fetch("CAPITALCOM:US100", "1d", 1, run=_runner(dxy), bridge_dir=_bridge(tmp_path))


def test_backend_selection_and_source_label(monkeypatch):
    calls = []
    def node(*a, **k):
        calls.append("node"); return ROWS
    def tvd(*a, **k):
        calls.append("tvd"); return ROWS
    def boom_node(*a, **k):
        calls.append("node"); raise RuntimeError("no node")
    monkeypatch.setattr(fb, "_tvapi_fetch", node); monkeypatch.setattr(fb, "_tvdatafeed_fetch", tvd)
    monkeypatch.delenv("TV_BACKEND", raising=False)
    rows, meta = fb.fetch("US100", "1m", 1, yf_fetch=boom)                    # default tv_fetch = dispatcher
    assert calls == ["node"] and meta["fallback"] is False and meta["source"].endswith("(TradingView-API)")
    calls.clear(); monkeypatch.setattr(fb, "_tvapi_fetch", boom_node)
    rows, meta = fb.fetch("US100", "1m", 1, yf_fetch=boom)                    # node fails -> tvDatafeed, still TradingView
    assert calls == ["node", "tvd"] and meta["fallback"] is False and meta["source"].endswith("(tvDatafeed)")
    calls.clear(); monkeypatch.setenv("TV_BACKEND", "tvdatafeed")
    fb.fetch("US100", "1m", 1, yf_fetch=boom)
    assert calls == ["tvd"]
    monkeypatch.setenv("TV_BACKEND", "node")
    rows, meta = fb.fetch("US100", "1m", 1, yf_fetch=lambda *a: ROWS)        # forced node fails -> yfinance FALLBACK
    assert meta["fallback"] is True and "no node" in meta["fallback_reason"]


def test_bad_backend_value_is_an_error_not_a_silent_default(monkeypatch):
    monkeypatch.setenv("TV_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="TV_BACKEND"):
        fb._tv_fetch("CAPITALCOM:US100", "1m", 1)


def test_monday_bars_after_the_weekend_reopen_are_accepted(tmp_path):
    # Real shapes seen 18 Nov 2026: CAPITALCOM:US100 Mon 16 Nov opens 01:00 SAST (Sun 18:00 NY), Tue 00:00;
    # TVC:DXY Mon 16 Nov opens 00:00 SAST (Sun 17:00 NY), Tue 02:00. Both label to the right trading dates.
    us = '[{"time":1794783600,"open":1,"high":2,"low":0.5,"close":1.5},{"time":1794866400,"open":1,"high":2,"low":0.5,"close":1.5}]'
    rows = fb._tvapi_fetch("CAPITALCOM:US100", "1d", 2, run=_runner(us), bridge_dir=_bridge(tmp_path))
    assert [r["time"] for r in fb.label_daily(rows)] == ["2026-11-16", "2026-11-17"]
    dx = '[{"time":1794780000,"open":1,"high":2,"low":0.5,"close":1.5},{"time":1794873600,"open":1,"high":2,"low":0.5,"close":1.5}]'
    rows = fb._tvapi_fetch("TVC:DXY", "1d", 2, run=_runner(dx), bridge_dir=_bridge(tmp_path))
    assert [r["time"] for r in fb.label_daily(rows)] == ["2026-11-16", "2026-11-17"]


def test_gold_daily_bars_open_at_the_18_ny_reopen(tmp_path):
    # CAPITALCOM:GOLD daily bars open 01:00 SAST every day (18:00 NY, seen 18 Nov 2026) -> default hours accept them
    assert fb.daily_open_hours("CAPITALCOM:GOLD") == (23, 0, 1)
    g = '[{"time":1794956400,"open":3848.4,"high":3860,"low":3760,"close":3770}]'          # Wed 18 Nov 2026 01:00 SAST
    rows = fb._tvapi_fetch("CAPITALCOM:GOLD", "1d", 1, run=_runner(g), bridge_dir=_bridge(tmp_path))
    assert fb.label_daily(rows)[0]["time"] == "2026-11-18"
