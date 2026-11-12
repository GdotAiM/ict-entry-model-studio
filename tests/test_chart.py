import pytest
import validate_setup as v

pytest.importorskip("matplotlib")
import render_chart as rc  # noqa: E402


def test_renders_png(tmp_path, example_setup, example_bars):
    example_setup["stop"] = 20051.0
    example_setup["targets"] = [{"name": "TP1", "price": 19970.0, "r": 2.24}, {"name": "TP2", "price": 19940.0, "r": 3.44}]
    out = rc.render(example_setup, example_bars, str(tmp_path / "c" / "x.png"))
    data = open(out, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 10_000


def test_no_bars_raises(tmp_path, example_setup):
    with pytest.raises(ValueError):
        rc.render(example_setup, [], str(tmp_path / "x.png"))
