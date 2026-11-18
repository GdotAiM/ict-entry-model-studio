"""Risk caps: correlated-group cap on real risk, 2% daily loss cap, 5% max drawdown, LOCKED state, 1.995R rounding.
All numbers are FABRICATED test values."""
import json
from datetime import datetime
import pytest
import position_size as ps
import validate_setup as v
import riskstate
import lifecycle, models, render_alert, check_alert, alert_log
import test_watch as tw

TG, cycle = tw.TG, tw.cycle
env, tg = tw.env, tw.tg
T = datetime.fromisoformat


# ------------------------------------------------------------- correlated group cap
def test_group_cap_counts_real_risk_reported_bug():
    """The reported case: 0.5% standalone + 3 x 0.25% = 1.25% used to pass as 'inside the 1% cap'."""
    opens = []
    sizes = []
    for inst in ("EURUSD", "GBPUSD", "XAUUSD", "EURUSD"):
        r = ps.risk_pct_for(inst, "long", opens)
        if r["allowed"]:
            opens.append((inst, "long", r["risk_pct"])); sizes.append(r["risk_pct"])
    assert sizes == [0.5, 0.25, 0.25] and sum(sizes) <= 1.0
    last = ps.risk_pct_for("EURUSD", "long", opens)
    assert last["allowed"] is False and last["group_used_pct"] == 1.0 and "1% group cap" in last["note"]


def test_group_cap_uses_recorded_risk_and_reports_totals():
    r = ps.risk_pct_for("GBPUSD", "short", [("EURUSD", "short", 0.5)])
    assert r["allowed"] and r["risk_pct"] == 0.25 and r["group_used_pct"] == 0.5 and r["group_after_pct"] == 0.75
    r = ps.risk_pct_for("GBPUSD", "short", [("EURUSD", "short", 0.25)] * 3)
    assert r["allowed"] and r["group_after_pct"] == 1.0
    assert ps.risk_pct_for("GBPUSD", "short", [("EURUSD", "short", 0.25)] * 4)["allowed"] is False   # max 4 trades


def test_cli_open_with_risk(capsys):
    assert ps.main(["--risk-pts", "10", "--instrument", "EURUSD", "--direction", "long",
                    "--open", "GBPUSD:long:0.5", "--open", "XAUUSD:long:0.25", "--open", "EURUSD:long:0.25"]) == 1
    assert ps.main(["--risk-pts", "10", "--instrument", "EURUSD", "--direction", "long", "--open", "GBPUSD:long:0.5"]) == 0


# ------------------------------------------------------------- 1.995R rounding
def test_1995R_is_not_2R():
    r = v.validate("short", 100.0, 105.0, [90.025], 0.0, 0.0)
    assert r["verdict"] == "NO TRADE" and r["targets"][0]["r"] == 1.99
    assert v.validate("short", 100.0, 105.0, [90.0], 0.0, 0.0)["verdict"] == "VALID"


def test_1995R_first_target_is_partial_and_never_shown_as_2R():
    r = v.validate("long", 100.0, 95.0, [109.975, 120.0], 0.0, 0.0)
    assert r["verdict"] == "VALID"
    tp1 = r["targets"][0]
    assert tp1["partial"] is True and tp1["r"] == 1.99 and "1.99R" in r["notes"][-1]


def test_float_noise_exact_2R_still_passes():
    # 0.3/0.15 style float noise must not reject a true 2R
    r = v.validate("long", 1.1000, 1.0985, [1.1030], 0.0, 0.0, decimals=5)
    assert r["verdict"] == "VALID"


# ------------------------------------------------------------- account state
def closed(sid, t, r, risk=0.5, event="STOP", targets=1, extra=()):
    evs = [{"event": "NEW", "time": t, "price": None, "r": None}, *extra, {"event": event, "time": t, "price": 1, "r": r}]
    return {"id": sid, "instrument": "US100", "direction": "short", "risk_pct": risk, "status": "closed_stop",
            "targets": [{"name": f"TP{i+1}", "price": 1} for i in range(targets)], "events": evs}


NOW = T("2026-01-15T10:00:00+02:00")


def test_daily_cap_locks_at_2pct():
    s = [closed(f"N{i}", "2026-01-15T08:0%d:00+02:00" % i, -1.0) for i in range(3)]
    rs = riskstate.risk_state(s, NOW)
    assert rs["daily_pct"] == -1.5 and not rs["locked"]
    rs = riskstate.risk_state(s + [closed("N9", "2026-01-15T09:00:00+02:00", -1.0)], NOW)
    assert rs["daily_pct"] == -2.0 and rs["locked"] and rs["lock"]["kind"] == "daily"
    assert rs["lock"]["id"] == "RISK-DAY-20260115" and "00:00 SAST" in rs["lock"]["until"]   # Jan: 17:00 NY = 00:00 SAST


def test_daily_cap_rolls_at_17_new_york_dst_correct():
    s = [closed(f"N{i}", "2026-07-15T21:00:00+02:00", -1.0) for i in range(4)]   # Jul: 15:00 NY, trading day 15 Jul
    assert riskstate.risk_state(s, T("2026-07-15T22:59:00+02:00"))["locked"]
    assert not riskstate.risk_state(s, T("2026-07-15T23:00:00+02:00"))["locked"]   # 17:00 NY (EDT) = 23:00 SAST
    s = [closed(f"N{i}", "2026-01-15T21:00:00+02:00", -1.0) for i in range(4)]
    assert riskstate.risk_state(s, T("2026-01-15T23:59:00+02:00"))["locked"]      # Jan: roll is 00:00 SAST
    assert not riskstate.risk_state(s, T("2026-01-16T00:00:00+02:00"))["locked"]


def test_partial_convention_matches_recap():
    s = closed("N1", "2026-01-15T08:00:00+02:00", -1.0, targets=2,
               extra=({"event": "TP1", "time": "2026-01-15T07:30:00+02:00", "price": 1, "r": 2.0},))
    assert riskstate.risk_state([s], NOW)["daily_pct"] == pytest.approx(0.5 * 2 * 0.5 + 0.5 * -1 * 0.5)


def test_drawdown_cap_locks_across_days_and_reset_clears():
    days = ["2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15"]
    s = [closed(f"N{i}{k}", f"{d}T08:0{k}:00+02:00", -1.0) for i, d in enumerate(days) for k in range(3)]   # 1.5%/day
    s.insert(0, closed("W1", "2026-01-09T08:00:00+02:00", 2.0))         # +1% first, so the peak is above start
    rs = riskstate.risk_state(s, NOW)
    assert rs["drawdown_pct"] >= 5.0 and rs["locked"] and rs["lock"]["kind"] == "drawdown"
    cfg = {"risk": {"dd_reset_at": "2026-01-15T09:00:00+02:00"}}
    rs = riskstate.risk_state(s, NOW, cfg)
    assert rs["drawdown_pct"] == 0 and not rs["locked"]


def test_headroom_counts_open_risk():
    s = [closed(f"N{i}", "2026-01-15T08:0%d:00+02:00" % i, -1.0) for i in range(2)]          # -1.0% today
    s.append({"id": "N5", "instrument": "US100", "direction": "short", "risk_pct": 0.5, "status": "filled", "targets": [{}, {}]})
    rs = riskstate.risk_state(s, NOW)
    assert rs["open_risk_pct"] == 0.5
    assert riskstate.headroom(rs, 0.5)["allowed"]                                            # -1 -0.5 -0.5 = -2.0 ok
    h = riskstate.headroom(rs, 0.75)
    assert not h["allowed"] and "daily" in h["reasons"][0]
    s[-1]["status"] = "tp1"                                                                   # half banked: 0.25 left
    assert riskstate.risk_state(s, NOW)["open_risk_pct"] == 0.25


# ------------------------------------------------------------- LOCKED state
def test_locked_is_terminal_and_has_a_stage():
    assert lifecycle.evaluate({"status": "locked"}, []) == ("locked", [])
    assert models.charter_stage("locked").startswith("Trade Planning (locked")


def test_lock_alert_passes_the_gate():
    rs = riskstate.risk_state([closed(f"N{i}", "2026-01-15T08:0%d:00+02:00" % i, -1.0) for i in range(4)], NOW)
    txt = render_alert.render_lock(rs)
    assert check_alert.check(txt, "LOCKED") == [] and "RISK LOCKED" in txt and "-2.00%" in txt
    assert "LOCKED" in alert_log.EVENTS


def seed(root, setups):
    d = root / "state" / "setups"; d.mkdir(parents=True, exist_ok=True)
    for s in setups:
        s.update(date="2026-01-15", killzone="London Open (x)", sweep={"extreme": 1}, entry=1, stop=1)
        (d / f"{s['id']}.json").write_text(json.dumps(s))
        for e in s["events"]:                       # already delivered earlier today
            alert_log.add(str(root / "state" / "alerts_log.csv"), {"date": "2026-01-15", "setup_id": s["id"], "event": e["event"],
                                                                   "instrument": "US100", "r": e.get("r")})


def test_watch_locks_blocks_new_setups_and_alerts_once(env, tg):
    seed(env, [closed(f"N{i}", "2026-01-15T08:0%d:00+02:00" % i, -1.0) for i in range(1, 5)])   # -2.0% today
    r = cycle(env, "09:27")
    assert r["risk"]["locked"] and r["new"] == [] and len(r["locked"]) == 1
    s = json.load(open(env / "state" / "setups" / f"{r['locked'][0]}.json"))
    assert s["status"] == "locked" and s["events"] == [] and s["charter_stage"].startswith("Trade Planning (locked")
    assert [x["event"] for x in r["outbox"]["sent"]] == ["LOCKED"] and tg.count("sendMessage") == 1
    r = cycle(env, "09:33")
    assert r["outbox"]["sent"] == [] and tg.count("sendMessage") == 1                            # one alert per lock
    rows = alert_log.read(str(env / "state" / "alerts_log.csv"))
    assert [(x["setup_id"], x["event"]) for x in rows][-1:] == [("RISK-DAY-20260115", "LOCKED")]
    assert sum(1 for x in rows if x["event"] == "LOCKED") == 1


def test_watch_skips_setup_that_would_break_daily_headroom(env, tg):
    seed(env, [closed(f"N{i}", "2026-01-15T08:0%d:00+02:00" % i, -1.0) for i in range(1, 4)]
         + [closed("N8", "2026-01-15T08:30:00+02:00", -1.0, risk=0.25)])                        # -1.75% today
    r = cycle(env, "09:27")
    assert not r["risk"]["locked"] and r["new"] == [] and r["outbox"]["sent"] == []
    assert r["risk"]["skipped"] and "daily" in r["risk"]["skipped"][0]["reasons"][0]


def test_recap_ignores_lock_rows():
    import recap
    rows = [{"date": "2026-01-15", "setup_id": "RISK-DAY-20260115", "event": "LOCKED", "instrument": "ALL", "session": "",
             "direction": "", "label": "", "model": "", "r": "", "target": ""}]
    assert recap.setup_results(rows) == []
