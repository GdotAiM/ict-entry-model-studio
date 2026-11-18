"""End-to-end offline replay of the pure-Python watch cycle on EXAMPLE (FABRICATED) bars. No network."""
import io, json, os, shutil
from datetime import datetime
import pytest
import watch, notify, alert_log
from conftest import ROOT

TG = {"TELEGRAM_BOT_TOKEN": "123:FAKE", "TELEGRAM_CHAT_ID": "42"}


@pytest.fixture
def env(tmp_path):
    b = tmp_path / "bars"; b.mkdir()
    shutil.copy(os.path.join(ROOT, "examples", "EXAMPLE_bars_US100_1m.csv"), b / "US100_1m.csv")
    shutil.copy(os.path.join(ROOT, "examples", "EXAMPLE_bars_US100_1d.csv"), b / "US100_1d.csv")
    return tmp_path


@pytest.fixture
def tg(monkeypatch):
    sent = []
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake(req, timeout=0):
        sent.append(req.full_url.rsplit("/", 1)[1]); return R(b'{"ok": true}')
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    return sent


def cycle(root, t, **kw):
    kw.setdefault("env", TG); kw.setdefault("charts", False)
    return watch.run_cycle(datetime.fromisoformat(f"2026-01-15T{t}:00+02:00"), root=str(root), bars_dir=str(root / "bars"),
                           instruments=["US100"], **kw)


def test_replay_new_fill_tp_and_dedupe(env, tg):
    r = cycle(env, "09:23")
    assert r["killzone"] == "London Open" and r["new"] == []          # raid only, no MSS yet: silent
    r = cycle(env, "09:27")
    assert r["new"] == ["N1"] and [x["event"] for x in r["outbox"]["sent"]] == ["NEW"]
    s = json.load(open(env / "state" / "setups" / "N1.json"))
    assert s["model"].startswith("Unicorn") and s["charter_model"].startswith("M13")
    assert s["charter_stage"] == "Trade Planning" and s["bias_label"] == "with-bias"
    assert s["entry"] == 20021.0 and s["stop"] == 20051.0 and s["targets"][0]["price"] == 19960.0
    assert s["spread"] == 1.5 and s["wick"] == 7.5                       # from config stop_buffer
    r = cycle(env, "09:29")
    assert r["new"] == [] and r["outbox"]["sent"] == []                  # nothing changed -> nothing sent
    r = cycle(env, "09:32")
    assert r["events"] == ["N1 FILLED"] and r["outbox"]["sent"][0]["event"] == "FILLED"
    assert json.load(open(env / "state" / "setups" / "N1.json"))["charter_stage"] == "Trade Execution"
    r = cycle(env, "09:38")
    assert r["events"] == ["N1 TP1"]
    rows = alert_log.read(str(env / "state" / "alerts_log.csv"))
    assert [(x["setup_id"], x["event"]) for x in rows] == [("N1", "NEW"), ("N1", "FILLED"), ("N1", "TP1")]
    assert tg.count("sendMessage") == 3
    assert open(env / "findings" / "setups.md").readline().startswith("Updated: 2026-01-15 09:38 SAST")


def test_failed_delivery_retries_in_order(env, tg):
    cycle(env, "09:27", env={})                                          # no channel configured -> not logged
    assert alert_log.read(str(env / "state" / "alerts_log.csv")) == []
    r = cycle(env, "09:32", env={})
    assert [x["event"] for x in r["outbox"]["failed"]] == ["NEW"]       # FILLED waits behind the undelivered NEW
    r = cycle(env, "09:33")
    assert [x["event"] for x in r["outbox"]["sent"]] == ["NEW", "FILLED"]


def test_dry_run_sends_and_logs_nothing(env, tg):
    r = cycle(env, "09:27", dry_run=True)
    assert [x["event"] for x in r["outbox"]["dry_run"]] == ["NEW"] and tg == []
    assert "Unicorn" in r["outbox"]["dry_run"][0]["text"]
    assert alert_log.read(str(env / "state" / "alerts_log.csv")) == []


def test_late_scanner_does_not_send_stale_burst(env, tg):
    r = cycle(env, "09:42")      # first run after the setup already hit both targets
    assert r["new"] == [] and tg == []


def test_outside_killzone_no_scan(env, tg):
    r = watch.run_cycle(datetime.fromisoformat("2026-01-15T13:00:00+02:00"), root=str(env), bars_dir=str(env / "bars"),
                        instruments=["US100"], env=TG, charts=False)
    assert r["killzone"] is None and r["new"] == []


def test_chart_rendered_for_alert(env, tg):
    pytest.importorskip("matplotlib")
    r = cycle(env, "09:27", charts=True)
    ch = r["outbox"]["sent"][0]["chart"]
    assert ch and os.path.exists(ch) and tg[:2] == ["sendPhoto", "sendMessage"]


# ---------------------------------------------------------------- regressions / extra replay paths
def _bars_from_example(env, keep_until, extra_rows):
    """Rewrite the US100 1m file: example bars up to `keep_until` (HH:MM) plus FABRICATED extra rows."""
    p = env / "bars" / "US100_1m.csv"
    lines = [ln for ln in open(p, newline="").read().splitlines() if ln.startswith("time") or ln[11:16] <= keep_until]
    p.write_text("\n".join(lines + extra_rows) + "\n")


def test_no_rows_up_to_now_is_a_clean_no_data_error(env):
    """Regression: this path used to raise NameError ('cut' undefined) instead of LookupError('NO DATA')."""
    cfg = watch.emconfig.load_config()
    b = watch.Bars(str(env / "bars"), datetime.fromisoformat("2026-01-15T08:00:00+02:00"), cfg)   # before the first bar
    with pytest.raises(LookupError, match="NO DATA"):
        b.get("US100", "1m")
    r = watch.run_cycle(datetime.fromisoformat("2026-01-15T08:00:00+02:00"), root=str(env), bars_dir=str(env / "bars"),
                        instruments=["US100"], env=TG, charts=False)
    assert r["new"] == [] and all("NO DATA" in e for e in r["errors"])


def _mark_fallback(env, inst="US100"):
    for iv in ("1m", "1d"):
        (env / "bars" / f"{inst}_{iv}.csv.meta.json").write_text(
            json.dumps({"source": "yfinance FALLBACK NQ=F (proxy)", "fallback": True}))


def test_fallback_blocked_for_futures_proxy_instruments(env, tg):
    _mark_fallback(env)
    r = cycle(env, "09:27")
    assert r["new"] == [] and tg == []
    assert r["errors"] and all("NO DATA" in e and "block_on_fallback" in e for e in r["errors"])
    assert alert_log.read(str(env / "state" / "alerts_log.csv")) == []


def test_fallback_blocks_lifecycle_of_open_setups_too(env, tg):
    cycle(env, "09:27")                                       # NEW delivered from (trusted) local data
    _mark_fallback(env)
    r = cycle(env, "09:32")                                   # bars now flagged fallback: no FILLED may be derived from them
    assert r["events"] == [] and r["errors"]
    assert json.load(open(env / "state" / "setups" / "N1.json"))["status"] == "pending"


def test_fallback_allowed_when_not_blocked_and_disclosed(env, tg):
    _mark_fallback(env)
    cfg = watch.emconfig.load_config()
    cfg["instruments"]["US100"]["block_on_fallback"] = False
    b = watch.Bars(str(env / "bars"), datetime.fromisoformat("2026-01-15T09:27:00+02:00"), cfg)
    rows, meta = b.get("US100", "1m")
    assert rows and meta["fallback"] is True


def test_replay_stop_path_and_recap(env, tg):
    import recap
    _bars_from_example(env, "09:31", ["2026-01-15T09:32:00+02:00,20021,20060,20015,20055"])
    cycle(env, "09:27"); cycle(env, "09:32")
    r = cycle(env, "09:34")
    assert r["events"] == ["N1 STOP"] and r["outbox"]["sent"][0]["event"] == "STOP"
    s = json.load(open(env / "state" / "setups" / "N1.json"))
    assert s["status"] == "closed_stop" and s["charter_stage"].startswith("Trade Management")
    res = recap.setup_results(alert_log.read(str(env / "state" / "alerts_log.csv")))
    assert res[0]["outcome"] == "STOP" and res[0]["r"] == -1.0 and res[0]["taken"] is True
    assert cycle(env, "09:35")["outbox"]["sent"] == []        # closed: nothing more is sent


def test_replay_cancelled_when_kill_zone_ends_unfilled(env, tg):
    import recap
    _bars_from_example(env, "09:29", ["2026-01-15T12:00:00+02:00,19990,19999,19985,19995"])
    cycle(env, "09:27")
    r = watch.run_cycle(datetime.fromisoformat("2026-01-15T12:01:00+02:00"), root=str(env), bars_dir=str(env / "bars"),
                        instruments=["US100"], env=TG, charts=False)
    assert r["events"] == ["N1 CANCELLED"] and r["outbox"]["sent"][0]["event"] == "CANCELLED"
    res = recap.setup_results(alert_log.read(str(env / "state" / "alerts_log.csv")))
    assert res[0]["outcome"] == "CANCELLED" and res[0]["taken"] is False and res[0]["r"] == 0.0


# ---------------------------------------------------------------- only closed bars are used
def at(root, hhmmss, **kw):
    kw.setdefault("env", TG); kw.setdefault("charts", False)
    return watch.run_cycle(datetime.fromisoformat(f"2026-01-15T{hhmmss}+02:00"), root=str(root), bars_dir=str(root / "bars"),
                           instruments=["US100"], **kw)


def test_bars_closed_boundary(env):
    cfg = watch.emconfig.load_config()
    def last(now):
        return watch.Bars(str(env / "bars"), datetime.fromisoformat(f"2026-01-15T{now}+02:00"), cfg).get("US100", "1m")[0][-1]["time"][11:16]
    assert last("09:31:59") == "09:30"        # the 09:31 bar is still forming
    assert last("09:32:00") == "09:31"        # closes exactly now
    assert last("09:32:30") == "09:31"


def test_forming_bar_is_not_used_and_last_checked_never_points_at_it(env, tg):
    at(env, "09:27:00")                                           # NEW
    r = at(env, "09:30:30")                                       # the 09:30 bar touches the entry but is still forming
    assert r["events"] == []
    s = json.load(open(env / "state" / "setups" / "N1.json"))
    assert s["status"] == "pending" and s["last_checked"].endswith("09:29:00+02:00")
    r = at(env, "09:31:00")                                       # now it is closed and is evaluated
    assert r["events"] == ["N1 FILLED"]


def test_stop_inside_forming_minute_is_not_missed(env, tg):
    """Old behaviour: the forming bar was recorded as last_checked and never re-read, so a late stop spike was lost."""
    _bars_from_example(env, "09:31", ["2026-01-15T09:32:00+02:00,20021,20060,20015,20055"])
    at(env, "09:27:00"); at(env, "09:32:00")                      # NEW, FILLED
    r = at(env, "09:32:30")                                       # 09:32 (the spike bar) still forming: ignored
    assert r["events"] == []
    r = at(env, "09:33:00")
    assert r["events"] == ["N1 STOP"]


def _dxy(points):
    """FABRICATED DXY 1m rows from (HH:MM, low, high)."""
    return [{"time": f"2026-01-15T{t}:00+02:00", "open": (lo + hi) / 2, "high": hi, "low": lo, "close": (lo + hi) / 2}
            for t, lo, hi in points]


def test_dxy_smt_for_usd_quote_pairs():
    since = "2026-01-15T00:00:00+02:00"
    base = [(f"09:{m:02d}", 99.00, 99.10) for m in range(0, 20)]                 # DXY low 99.00 before the sweep
    held = _dxy(base + [("09:20", 99.02, 99.08), ("09:21", 99.03, 99.09)])       # EURUSD sweeps its high, DXY holds its low
    r = watch.dxy_smt("short", "2026-01-15T09:21:00+02:00", held, since, "EURUSD")
    assert r["smt"] is True and "divergence supports the short" in r["text"]
    made = _dxy(base + [("09:20", 98.95, 99.05), ("09:21", 98.97, 99.04)])       # DXY made the mirror new low too
    assert watch.dxy_smt("short", "2026-01-15T09:21:00+02:00", made, since)["smt"] is False
    # long on the pair = sell-side sweep; the mirror is a new DXY high
    up = _dxy(base + [("09:20", 99.05, 99.15)])
    assert watch.dxy_smt("long", "2026-01-15T09:20:00+02:00", up, since)["smt"] is False
    assert watch.dxy_smt("long", "2026-01-15T09:20:00+02:00", held, since)["smt"] is True
    assert watch.dxy_smt("short", "2026-01-15T09:21:00+02:00", held[:3], since) is None   # not enough history: no call


def test_scan_list_skips_dxy_and_includes_gold(env, tg):
    seen = []
    orig = watch.scan_instrument
    try:
        watch.scan_instrument = lambda inst, *a, **k: seen.append(inst) or []
        watch.run_cycle(datetime.fromisoformat("2026-01-15T09:27:00+02:00"), root=str(env), bars_dir=str(env / "bars"),
                        dry_run=True, env=TG, charts=False)
    finally:
        watch.scan_instrument = orig
    assert seen == ["US100", "US500", "EURUSD", "GBPUSD", "XAUUSD"]
