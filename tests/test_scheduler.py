from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
import scheduler as sc

SAST = ZoneInfo("Africa/Johannesburg")
W = ("London Open", "NY AM")


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=SAST)


def test_slots_est_and_edt():
    est = sc.slots(date(2026, 11, 12), W, 6)
    assert est[0][0].strftime("%H:%M") == "09:00" and est[0][1] == "London Open"
    names = [(x.strftime("%H:%M"), n) for x, n in est]
    assert ("11:54", "London Open") in names and ("12:00", "London Open close-out") in names
    assert ("14:00", "NY AM") in names and ("17:00", "NY AM close-out") in names
    assert len(est) == 2 * (30 + 1)
    edt = sc.slots(date(2026, 10, 14), W, 6)
    assert edt[0][0].strftime("%H:%M") == "08:00" and ("16:00", "NY AM close-out") in [(x.strftime("%H:%M"), n) for x, n in edt]


def test_weekend_skipped():
    assert sc.slots(date(2026, 11, 14), W, 6) == []
    assert sc.next_runs(t("2026-11-13T17:30:00"), W, 6, 1)[0]["sast"].startswith("Mon 2026-11-16 09:00")


def test_due_with_tolerance():
    assert sc.is_due(t("2026-11-12T09:06:30"), W, 6) == "London Open"
    assert sc.is_due(t("2026-11-12T09:08:59"), W, 6) == "London Open"   # cron jitter up to 2 min
    assert sc.is_due(t("2026-11-12T09:09:00"), W, 6) is None
    assert sc.is_due(t("2026-11-12T13:00:00"), W, 6) is None
    assert sc.is_due(t("2026-11-12T12:00:00"), W, 6) == "London Open close-out"
    assert sc.is_due(t("2026-11-02T13:00:00"), W, 6) is None             # Mon after US DST ended: NY AM is 14:00
    assert sc.is_due(t("2026-10-30T13:00:00"), W, 6) == "NY AM"          # still EDT


def test_tick_runs_once_per_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "LOCK", str(tmp_path / ".lock")); monkeypatch.setattr(sc, "LAST", str(tmp_path / ".last"))
    monkeypatch.setattr(sc, "settings", lambda: (W, 6))
    ran = []
    class P: returncode = 0
    fake = lambda cmd, cwd=None, timeout=0: ran.append(cmd) or P()
    assert sc.tick(t("2026-11-12T10:00:10"), "python", run=fake)["ran"] is True
    assert sc.tick(t("2026-11-12T10:01:10"), "python", run=fake)["reason"].startswith("slot already run")
    assert sc.tick(t("2026-11-12T10:03:30"), "python", run=fake)["ran"] is False
    assert len(ran) == 1 and ran[0][1].endswith("watch.py") and "--quiet" in ran[0]
    assert not (tmp_path / ".lock").exists()


def test_tick_respects_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "LOCK", str(tmp_path / ".lock")); monkeypatch.setattr(sc, "LAST", str(tmp_path / ".last"))
    monkeypatch.setattr(sc, "settings", lambda: (W, 6))
    (tmp_path / ".lock").write_text(f"999 {t('2026-11-12T09:58:00').isoformat()}")
    assert sc.tick(t("2026-11-12T10:00:00"), run=lambda *a, **k: None)["reason"].startswith("previous run")


def test_runner_commands():
    assert sc.runner_cmd("claude", env={"CLAUDE_ARGS": "--max-turns 30"}) == ["claude", "-p", "/kz-watch", "--max-turns", "30"]
    assert sc.runner_cmd("python", dry_run=True)[-1] == "--dry-run"
