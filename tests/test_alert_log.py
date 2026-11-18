import alert_log as al
import recap


def row(sid, ev, r="", target="20 / 10", label="with-bias", date="2026-01-15"):
    return {"date": date, "session": "London Open", "instrument": "US100", "setup_id": sid, "direction": "short",
            "label": label, "model": "core", "event": ev, "alert_time_sast": "09:30", "target": target, "r": r}


def test_dedupe(tmp_path):
    p = str(tmp_path / "log.csv")
    assert not al.is_logged(p, "N1", "NEW")
    assert al.add(p, row("N1", "NEW")) is True
    assert al.add(p, row("N1", "new")) is False          # case-insensitive event, same setup -> duplicate
    assert al.is_logged(p, "N1", "NEW")
    assert al.add(p, row("N1", "FILLED")) is True
    assert al.add(p, row("N2", "NEW")) is True
    assert len(al.read(p)) == 3
    assert open(p).read().splitlines()[0].startswith("date,session,instrument,setup_id")


def test_rejects_unknown_event(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        al.add(str(tmp_path / "l.csv"), row("N1", "MAYBE"))


def test_cli_check_exit_codes(tmp_path, capsys):
    p = str(tmp_path / "l.csv")
    assert al.main(["--log", p, "check", "--id", "N9", "--event", "STOP"]) == 0
    assert al.main(["--log", p, "add", "--id", "N9", "--event", "STOP", "--r", "-1"]) == 0
    assert al.main(["--log", p, "check", "--id", "N9", "--event", "STOP"]) == 1
    assert al.main(["--log", p, "add", "--id", "N9", "--event", "STOP"]) == 1


def test_recap_r_and_labels():
    rows = [row("A", "NEW"), row("A", "FILLED"), row("A", "TP1", "2.0"), row("A", "STOP", "-1"),     # 0.5*2 + 0.5*-1 = 0.5
            row("B", "NEW", label="COUNTER-TREND"), row("B", "FILLED"), row("B", "STOP", "-1"),
            row("C", "NEW"), row("C", "VOID"),
            row("D", "NEW", target="20"), row("D", "FILLED"), row("D", "TP1", "3.0", target="20"),   # single target
            row("E", "NEW"), row("E", "FILLED")]
    res = {r["setup_id"]: r for r in recap.setup_results(rows)}
    assert res["A"]["r"] == 0.5 and res["B"]["r"] == -1.0 and res["C"]["taken"] is False
    assert res["D"]["r"] == 3.0 and res["E"]["outcome"] == "OPEN"
    by = recap.summarise(list(res.values()), "label")
    assert by["COUNTER-TREND"] == {"trades": 1, "wins": 0, "losses": 1, "total_r": -1.0}


def test_n_targets_counts_prices_not_slashes():
    assert al.n_targets({"target": "20 / 10"}) == 2
    assert al.n_targets({"target": "19960.0"}) == 1
    assert al.n_targets({"target": ""}) == 0 and al.n_targets({}) == 0


def test_recap_single_vs_two_targets():
    one = recap.setup_results([row("A1", "NEW", target="19960.0"), row("A1", "TP1", r="2.0", target="19960.0")])
    assert one[0]["outcome"] == "TP1" and one[0]["r"] == 2.0                      # single target: TP1 closes the trade
    two = recap.setup_results([row("A2", "NEW"), row("A2", "FILLED"), row("A2", "TP1", r="2.0")])
    assert two[0]["outcome"] == "OPEN" and two[0]["r"] is None                    # two targets, only TP1 so far
