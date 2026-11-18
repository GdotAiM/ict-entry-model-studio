import json, os
import emconfig
import validate_setup as v
from conftest import ROOT, EXAMPLE_SETUP


def test_every_tradeable_instrument_has_a_default_buffer():
    cfg = emconfig.load_config(local=None)
    for name, spec in cfg["instruments"].items():
        sb = spec["stop_buffer"]
        assert {"spread", "wick_1m", "status"} <= set(sb)
        if not spec.get("confluence_only"):
            assert "DEFAULT" in sb["status"] and sb["spread"] > 0 and sb["wick_1m"] > 0
    assert "DEFAULT" in cfg["_comment"] and "broker" in cfg["_comment"]
    assert emconfig.stop_buffer(cfg, "US100")["total"] == 9.0
    assert emconfig.stop_buffer(cfg, "CAPITALCOM:GOLD")["total"] == 1.3


def test_local_override_merges(tmp_path):
    base = os.path.join(ROOT, "config", "instruments.json")
    loc = tmp_path / "local.json"
    loc.write_text(json.dumps({"instruments": {"US100": {"stop_buffer": {"spread": 2.0, "status": "broker: 2.0 pts"}}}}))
    cfg = emconfig.load_config(base, str(loc))
    sb = emconfig.stop_buffer(cfg, "US100")
    assert sb == {"spread": 2.0, "wick_1m": 7.5, "total": 9.5, "status": "broker: 2.0 pts", "is_default": False}
    assert cfg["instruments"]["US100"]["tv"] == "CAPITALCOM:US100"


def test_validator_reads_config_buffers(tmp_path, capsys):
    p = tmp_path / "s.json"
    p.write_text(open(EXAMPLE_SETUP).read())
    assert v.main(["--setup", str(p), "--write"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stop"] == 20051.0 and out["stop_buffer_source"].startswith("config/instruments.json")
    assert any("DEFAULT" in n for n in out["notes"])
    saved = json.load(open(p))
    assert saved["spread"] == 1.5 and saved["wick"] == 7.5


def test_futures_proxy_instruments_block_fallback():
    cfg = emconfig.load_config(local=None)
    ins = cfg["instruments"]
    assert ins["US100"]["block_on_fallback"] is True and ins["XAUUSD"]["block_on_fallback"] is True
    assert not ins["EURUSD"].get("block_on_fallback") and not ins["GBPUSD"].get("block_on_fallback")


def test_watchlist_is_us100_us500_eu_gu_dxy():
    cfg = emconfig.load_config(local=None)
    assert emconfig.watchlist(cfg) == ["US100", "US500", "EURUSD", "GBPUSD", "XAUUSD", "DXY"]
    assert emconfig.tradeable(cfg) == ["US100", "US500", "EURUSD", "GBPUSD", "XAUUSD"]  # DXY: no setups, no alerts
    ins = cfg["instruments"]
    assert ins["XAUUSD"].get("enabled", True) is True and ins["XAUUSD"]["tv"] == "CAPITALCOM:GOLD"   # gold back on
    assert ins["XAUUSD"]["block_on_fallback"] is True and ins["XAUUSD"]["dxy_relation"] == "inverse"
    assert emconfig.corr_group(cfg, "XAUUSD") == "gold"
    assert "NAS100" not in ins
    assert [ins[i]["tv"] for i in ("US100", "US500", "EURUSD", "GBPUSD", "DXY")] == [
        "CAPITALCOM:US100", "CAPITALCOM:US500", "CAPITALCOM:EURUSD", "CAPITALCOM:GBPUSD", "TVC:DXY"]
    assert ins["US500"]["block_on_fallback"] is True and ins["US500"]["yf"] == "ES=F"
    assert emconfig.corr_group(cfg, "US500") == emconfig.corr_group(cfg, "US100") == "US-indices"
    assert emconfig.corr_group(cfg, "EURUSD") == emconfig.corr_group(cfg, "GBPUSD") == "USD"
    assert ins["EURUSD"]["dxy_relation"] == ins["GBPUSD"]["dxy_relation"] == "inverse"
    for i in ("US100", "US500", "EURUSD", "GBPUSD", "XAUUSD"):
        assert "PLACEHOLDER" in ins[i]["stop_buffer"]["status"]
    assert emconfig.stop_buffer(cfg, "US500")["total"] == 2.5


def test_switching_an_instrument_off_via_local(tmp_path):
    loc = tmp_path / "local.json"
    loc.write_text(json.dumps({"instruments": {"XAUUSD": {"enabled": False}}}))
    cfg = emconfig.load_config(os.path.join(ROOT, "config", "instruments.json"), str(loc))
    assert "XAUUSD" not in emconfig.tradeable(cfg) and "XAUUSD" not in emconfig.watchlist(cfg)
    assert "XAUUSD" in cfg["instruments"]                                              # still in config
