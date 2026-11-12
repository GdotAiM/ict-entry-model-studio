import lifecycle as lc
from conftest import bar

T = lambda m: f"2026-01-15T09:{m:02d}:00+02:00"  # FABRICATED times


def test_example_runs_fill_tp1_tp2(example_setup, example_bars):
    example_setup.update(stop=20051.0)
    st, ev = lc.evaluate(example_setup, example_bars)
    assert st == "closed_tp2"
    assert [e["event"] for e in ev] == ["FILLED", "TP1", "TP2"]
    assert ev[0]["time"] == T(30) and ev[1]["r"] == 2.24


def test_void_when_tp1_trades_first(short_setup):
    st, ev = lc.evaluate(short_setup, [bar(T(1), 95, 98, 79, 80)])
    assert st == "void" and ev[0]["event"] == "VOID" and ev[0]["r"] == 0.0


def test_invalid_on_gap_through_raid(short_setup):
    st, ev = lc.evaluate(short_setup, [bar(T(1), 106, 108, 105.5, 107)])
    assert st == "invalid"


def test_fill_then_stop_same_bar(short_setup):
    st, ev = lc.evaluate(short_setup, [bar(T(1), 99, 111, 98, 108)])
    assert [e["event"] for e in ev] == ["FILLED", "STOP"] and st == "closed_stop"


def test_stop_wins_ambiguous_bar(short_setup):
    bars = [bar(T(1), 99, 100.5, 98, 99), bar(T(2), 99, 112, 60, 90)]
    st, ev = lc.evaluate(short_setup, bars)
    assert ev[-1]["event"] == "STOP" and ev[-1]["r"] == -1.0


def test_gap_stop_worse_than_minus_1r(short_setup):
    bars = [bar(T(1), 99, 100.5, 98, 99), bar(T(2), 115, 116, 113, 114)]
    st, ev = lc.evaluate(short_setup, bars)
    assert ev[-1]["event"] == "STOP" and ev[-1]["r"] == -1.5 and "gap" in ev[-1]["note"]


def test_tp1_then_rest_stopped(short_setup):
    bars = [bar(T(1), 99, 100.5, 98, 99), bar(T(2), 99, 99, 79, 85), bar(T(3), 85, 111, 84, 109)]
    st, ev = lc.evaluate(short_setup, bars)
    assert [e["event"] for e in ev] == ["FILLED", "TP1", "STOP"] and ev[1]["r"] == 2.0 and st == "closed_stop"


def test_breakeven_after_tp1_when_hilda_moves_stop(short_setup):
    short_setup["move_to_be_after_tp1"] = True
    bars = [bar(T(1), 99, 100.5, 98, 99), bar(T(2), 99, 99, 79, 85), bar(T(3), 85, 101, 84, 100.5)]
    st, ev = lc.evaluate(short_setup, bars)
    assert ev[-1]["event"] == "BE" and st == "closed_be"


def test_cancel_at_killzone_end(short_setup):
    bars = [bar(T(1), 95, 97, 94, 96), bar("2026-01-15T12:00:00+02:00", 96, 97, 95, 96)]
    st, ev = lc.evaluate(short_setup, bars, kz_end="2026-01-15T12:00:00+02:00")
    assert st == "cancelled" and ev[0]["event"] == "CANCELLED"


def test_only_new_bars_and_terminal_noop(short_setup):
    short_setup["last_checked"] = T(5)
    assert lc.evaluate(short_setup, [bar(T(4), 95, 98, 79, 80)]) == ("pending", [])
    short_setup["status"] = "closed_stop"
    assert lc.evaluate(short_setup, [bar(T(9), 95, 98, 79, 80)]) == ("closed_stop", [])


def test_long_mirror():
    s = {"direction": "long", "entry": 100.0, "stop": 90.0, "sweep": {"extreme": 95.0},
         "targets": [{"name": "TP1", "price": 120.0}], "status": "pending", "events": []}
    st, ev = lc.evaluate(s, [bar(T(1), 101, 102, 99.5, 101), bar(T(2), 101, 121, 100, 119)])
    assert st == "closed_tp1" and [e["event"] for e in ev] == ["FILLED", "TP1"] and ev[1]["r"] == 2.0


def test_cli_write(tmp_path, short_setup):
    import json
    sp, bp = tmp_path / "s.json", tmp_path / "b.csv"
    sp.write_text(json.dumps(short_setup))
    bp.write_text(f"time,open,high,low,close\n{T(1)},99,100.5,98,99\n")
    assert lc.main(["--setup", str(sp), "--bars", str(bp), "--write"]) == 0
    saved = json.loads(sp.read_text())
    assert saved["status"] == "filled" and saved["last_checked"] == T(1)
    assert lc.main(["--setup", str(sp), "--bars", str(bp)]) == 1  # nothing new -> no re-alert
