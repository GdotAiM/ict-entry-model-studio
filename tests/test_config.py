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
    assert emconfig.stop_buffer(cfg, "NAS100")["total"] == 9.0
    assert emconfig.stop_buffer(cfg, "OANDA:XAUUSD")["total"] == 1.3


def test_local_override_merges(tmp_path):
    base = os.path.join(ROOT, "config", "instruments.json")
    loc = tmp_path / "local.json"
    loc.write_text(json.dumps({"instruments": {"NAS100": {"stop_buffer": {"spread": 2.0, "status": "broker: 2.0 pts"}}}}))
    cfg = emconfig.load_config(base, str(loc))
    sb = emconfig.stop_buffer(cfg, "NAS100")
    assert sb == {"spread": 2.0, "wick_1m": 7.5, "total": 9.5, "status": "broker: 2.0 pts", "is_default": False}
    assert cfg["instruments"]["NAS100"]["tv"] == "OANDA:NAS100USD"


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
    assert ins["NAS100"]["block_on_fallback"] is True and ins["XAUUSD"]["block_on_fallback"] is True
    assert not ins["EURUSD"].get("block_on_fallback") and not ins["GBPUSD"].get("block_on_fallback")
