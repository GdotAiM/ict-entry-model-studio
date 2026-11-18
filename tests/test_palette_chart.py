"""Shared status palette + the alert chart image (numbers, countdown, no look-ahead). EXAMPLE (FABRICATED) data."""
from datetime import datetime
import pytest
import palette
import lifecycle

pytest.importorskip("matplotlib")
import render_chart as rc  # noqa: E402


def test_every_status_and_event_has_a_colour():
    for st in lifecycle.TERMINAL | {"pending", "filled", "tp1"}:
        assert palette.status_key(st) in palette.STATUS
    for ev in ("NEW", "FILLED", "TP1", "TP2", "TP3", "STOP", "BE", "VOID", "INVALID", "CANCELLED", "LOCKED"):
        assert palette.status_key(ev) in palette.STATUS
    assert palette.colors("closed_tp2") == palette.colors("TP1") and palette.colors("STOP")["fg"] == "#f25555"
    css = palette.css()
    assert all(f".p-{k}{{" in css for k in palette.STATUS) and "--bg:" in css


def test_countdown_text_and_progress():
    s = {"killzone": "London Open (02:00-05:00 NY = 09:00-12:00 SAST)", "killzone_end": "2026-01-15T12:00:00+02:00"}
    c = rc.countdown(s, datetime.fromisoformat("2026-01-15T09:57:00+02:00"), "NEW")
    assert c["left"] == "2h 03m left" and c["name"] == "London Open" and "auto-cancel" in c["note"]
    assert abs(c["frac"] - 57 / 180) < 1e-9 and "02:00-05:00 NY" in c["window"]
    c = rc.countdown(s, datetime.fromisoformat("2026-01-15T11:55:00+02:00"), "FILLED")
    assert c["left"] == "5m left" and "tracking" in c["note"]
    assert rc.countdown(s, datetime.fromisoformat("2026-01-15T12:00:00+02:00"), "NEW")["left"] == "kill zone over"


def test_countdown_ny_window_is_dst_correct():
    s = {"killzone": "NY AM (07:00-10:00 NY = 13:00-16:00 SAST)", "killzone_end": "2026-07-15T16:00:00+02:00"}
    c = rc.countdown(s, datetime.fromisoformat("2026-07-15T14:00:00+02:00"), "NEW")
    assert "07:00-10:00 NY" in c["window"] and c["left"] == "2h 00m left"


def test_chart_draws_only_bars_closed_by_the_alert_time(tmp_path, example_setup, example_bars, monkeypatch):
    seen = {}
    example_setup.update(stop=20051.0, targets=[{"name": "TP1", "price": 19970.0, "r": 2.24}],
                         events=[{"event": "NEW", "time": "2026-01-15T09:27:00+02:00"}],
                         killzone_end="2026-01-15T12:00:00+02:00")
    import matplotlib.axes
    orig = matplotlib.axes.Axes.set_xlim
    def spy(self, *a, **k):
        seen["xlim"] = a
        return orig(self, *a, **k)
    monkeypatch.setattr(matplotlib.axes.Axes, "set_xlim", spy)
    out = rc.render(example_setup, example_bars, str(tmp_path / "x.png"), "NEW")
    n_closed = sum(1 for b in example_bars if b["time"] < "2026-01-15T09:27:00+02:00")
    assert seen["xlim"] == (-1, n_closed + 1) and n_closed < len(example_bars)
    assert open(out, "rb").read()[:4] == b"\x89PNG"
