"""Close-out timing: an unfilled setup is CANCELLED (and alerted) on the close-out run at the kill-zone END minute,
when the bar that opens at the end is still forming and is not read. FABRICATED data, no network."""
import json
from datetime import date, datetime, timedelta
import pytest
import killzone
import lifecycle as lc
import watch, alert_log
from conftest import bar
import test_watch as tw

TG, _bars_from_example = tw.TG, tw._bars_from_example
env, tg = tw.env, tw.tg                           # reuse the replay fixtures from test_watch.py

LONDON_END = "2026-01-15T12:00:00+02:00"          # London Open 02:00-05:00 NY = 09:00-12:00 SAST (EST)
NYAM_END = "2026-01-15T17:00:00+02:00"            # NY AM 07:00-10:00 NY = 14:00-17:00 SAST (EST)
# Bars after each kill zone (prices stay below the 20021 entry and above TP1, so nothing fills, voids or invalidates)
AFTER = ["2026-01-15T12:00:00+02:00,19990,19995,19985,19992",
         "2026-01-15T17:00:00+02:00,19990,19995,19985,19992",
         "2026-01-15T17:01:00+02:00,19992,19996,19988,19990",
         "2026-01-16T08:59:00+02:00,19990,19994,19986,19991"]


# ---------------------------------------------------------------- lifecycle (pure)
def test_cancel_by_clock_at_end_minute_without_a_post_end_bar(short_setup):
    bars = [bar("2026-01-15T11:59:00+02:00", 95, 97, 94, 96)]          # last CLOSED bar at 12:00:30
    st, ev = lc.evaluate(short_setup, bars, LONDON_END, now="2026-01-15T12:00:30+02:00")
    assert st == "cancelled" and ev == [{"event": "CANCELLED", "time": LONDON_END, "price": None, "r": 0.0,
                                         "note": "kill zone ended unfilled"}]


def test_no_cancel_before_the_end(short_setup):
    bars = [bar("2026-01-15T11:58:00+02:00", 95, 97, 94, 96)]
    assert lc.evaluate(short_setup, bars, LONDON_END, now="2026-01-15T11:59:30+02:00") == ("pending", [])


def test_fill_in_the_last_minute_beats_the_cancel(short_setup):
    bars = [bar("2026-01-15T11:59:00+02:00", 99, 100.5, 98, 99)]      # touches the 100 entry
    st, ev = lc.evaluate(short_setup, bars, LONDON_END, now="2026-01-15T12:00:30+02:00")
    assert st == "filled" and [e["event"] for e in ev] == ["FILLED"]


def test_filled_trade_is_not_cancelled_at_the_end(short_setup):
    short_setup["status"] = "filled"
    assert lc.evaluate(short_setup, [], LONDON_END, now="2026-01-15T12:30:00+02:00") == ("filled", [])


def test_without_now_old_behaviour_is_kept(short_setup):
    assert lc.evaluate(short_setup, [bar("2026-01-15T11:59:00+02:00", 95, 97, 94, 96)], LONDON_END) == ("pending", [])


@pytest.mark.parametrize("day, end_hhmm", [(date(2026, 1, 15), "17:00"),    # EST: NY AM ends 10:00 NY = 17:00 SAST
                                           (date(2026, 6, 15), "16:00")])   # EDT: NY AM ends 10:00 NY = 16:00 SAST
def test_ny_am_end_is_dst_correct(short_setup, day, end_hhmm):
    end = next(k for k in killzone.killzones_for(day) if k["name"] == "NY AM")["end_sast"]
    assert end.strftime("%H:%M") == end_hhmm
    d = day.isoformat()
    before = end - timedelta(seconds=30)
    assert lc.evaluate(short_setup, [], end.isoformat(), now=before) == ("pending", [])
    st, ev = lc.evaluate(short_setup, [], end.isoformat(), now=f"{d}T{end_hhmm}:30+02:00")
    assert st == "cancelled" and ev[0]["time"] == f"{d}T{end_hhmm}:00+02:00"


def test_cli_cancels_by_clock(tmp_path, short_setup):
    short_setup["killzone_end"] = LONDON_END
    sp, bp = tmp_path / "s.json", tmp_path / "b.csv"
    sp.write_text(json.dumps(short_setup))
    bp.write_text("time,open,high,low,close\n2026-01-15T11:59:00+02:00,95,97,94,96\n")
    assert lc.main(["--setup", str(sp), "--bars", str(bp), "--now", "2026-01-15T12:00:30+02:00", "--write"]) == 0
    assert json.loads(sp.read_text())["status"] == "cancelled"


# ---------------------------------------------------------------- full watch cycle
def run(env, iso):
    return watch.run_cycle(datetime.fromisoformat(iso), root=str(env), bars_dir=str(env / "bars"),
                           instruments=["US100"], env=TG, charts=False)


def _pending_setup(env, killzone_end=None):
    _bars_from_example(env, "09:29", AFTER)
    r = run(env, "2026-01-15T09:27:00+02:00")
    assert r["new"] == ["N1"] and [x["event"] for x in r["outbox"]["sent"]] == ["NEW"]
    p = env / "state" / "setups" / "N1.json"
    if killzone_end:                       # same setup, as if it had been found in NY AM
        s = json.loads(p.read_text())
        s["killzone"], s["killzone_end"] = "NY AM (07:00-10:00 NY = 14:00-17:00 SAST)", killzone_end
        p.write_text(json.dumps(s))
    return p


def _cancel_rows(env):
    return [r for r in alert_log.read(str(env / "state" / "alerts_log.csv")) if r["event"] == "CANCELLED"]


def test_london_close_out_at_exact_end_minute(env, tg):
    p = _pending_setup(env)
    r = run(env, "2026-01-15T12:00:30+02:00")                # close-out slot; the 12:00 bar is still forming
    assert r["events"] == ["N1 CANCELLED"] and [x["event"] for x in r["outbox"]["sent"]] == ["CANCELLED"]
    assert json.loads(p.read_text())["status"] == "cancelled" and len(_cancel_rows(env)) == 1


def test_ny_am_close_out_at_exact_end_minute(env, tg):
    p = _pending_setup(env, NYAM_END)
    assert run(env, "2026-01-15T16:59:30+02:00")["events"] == []     # still inside NY AM
    r = run(env, "2026-01-15T17:00:30+02:00")
    assert r["events"] == ["N1 CANCELLED"] and [x["event"] for x in r["outbox"]["sent"]] == ["CANCELLED"]
    ev = json.loads(p.read_text())["events"][-1]
    assert ev["time"] == NYAM_END and ev["detected_at"] == "2026-01-15T17:00:30+02:00"


def test_late_detected_cancel_is_not_lost_to_max_age(env, tg):
    """The 17:00 close-out run was missed; the next run is 16 hours later, well past MAX_AGE (6h) from the event time."""
    _pending_setup(env, NYAM_END)
    r = run(env, "2026-01-16T09:00:30+02:00")
    assert r["events"] == ["N1 CANCELLED"]
    assert [x["event"] for x in r["outbox"]["sent"]] == ["CANCELLED"] and r["outbox"]["expired"] == []
    assert len(_cancel_rows(env)) == 1


def test_no_duplicate_cancel_on_later_runs(env, tg):
    _pending_setup(env, NYAM_END)
    run(env, "2026-01-15T17:00:30+02:00")
    sends = tg.count("sendMessage")
    for iso in ("2026-01-15T17:06:30+02:00", "2026-01-16T09:00:30+02:00", "2026-01-16T09:06:30+02:00"):
        r = run(env, iso)
        assert r["events"] == [] and r["outbox"]["sent"] == [] and r["outbox"]["expired"] == []
    assert tg.count("sendMessage") == sends and len(_cancel_rows(env)) == 1


def test_undelivered_event_still_expires_after_max_age(env, tg):
    """MAX_AGE still protects against stale bursts: an event detected long ago and never delivered is not sent."""
    _pending_setup(env, NYAM_END)
    p = env / "state" / "setups" / "N1.json"
    s = json.loads(p.read_text())
    s["status"] = "cancelled"
    s["events"].append({"event": "CANCELLED", "time": NYAM_END, "price": None, "r": 0.0, "note": "kill zone ended unfilled",
                        "detected_at": "2026-01-15T17:00:30+02:00"})
    p.write_text(json.dumps(s))
    r = run(env, "2026-01-16T09:00:30+02:00")
    assert r["outbox"]["sent"] == [] and [x["event"] for x in r["outbox"]["expired"]] == ["CANCELLED"]
