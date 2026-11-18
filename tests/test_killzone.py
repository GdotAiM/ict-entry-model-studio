from datetime import date, datetime
import killzone as kz

SAST = kz.SAST


def _sast(kzs, name):
    k = next(k for k in kzs if k["name"] == name)
    return f'{k["start_sast"]:%H:%M}-{k["end_sast"]:%H:%M}'


def test_offset_edt_vs_est():
    assert kz.offset_hours(date(2026, 10, 14)) == 6   # EDT
    assert kz.offset_hours(date(2026, 11, 12)) == 7   # EST (US DST ended 1 Nov 2026)


def test_ny_to_sast_dst():
    assert kz.ny_to_sast("07:00", date(2026, 10, 30)).strftime("%H:%M") == "13:00"
    assert kz.ny_to_sast("07:00", date(2026, 11, 2)).strftime("%H:%M") == "14:00"
    assert kz.ny_to_sast("09:30", date(2026, 3, 9)).strftime("%H:%M") == "15:30"  # US DST started 8 Mar 2026


def test_table_est_matches_ntlosos_windows():
    k = kz.killzones_for(date(2026, 11, 12))
    assert _sast(k, "Asian") == "03:00-07:00"
    assert _sast(k, "London Open") == "09:00-12:00"
    assert _sast(k, "NY AM") == "14:00-17:00"
    assert _sast(k, "Silver Bullet") == "17:00-18:00"
    assert _sast(k, "London Close") == "17:00-19:00"
    assert _sast(k, "NY PM") == "20:30-23:00"


def test_table_edt():
    k = kz.killzones_for(date(2026, 10, 14))
    assert _sast(k, "Asian") == "02:00-06:00"
    assert _sast(k, "London Open") == "08:00-11:00"
    assert _sast(k, "NY AM") == "13:00-16:00"
    assert _sast(k, "NY PM") == "19:30-22:00"


def test_asian_starts_previous_ny_evening():
    k = next(x for x in kz.killzones_for(date(2026, 11, 12)) if x["name"] == "Asian")
    assert k["start_sast"].date() == date(2026, 11, 12) and k["start_sast"].hour == 3


def test_active_and_watched():
    t = datetime(2026, 11, 12, 11, 30, tzinfo=SAST)
    assert kz.in_watched_killzone(t)["name"] == "London Open"
    assert kz.in_watched_killzone(datetime(2026, 11, 12, 12, 0, tzinfo=SAST)) is None  # end exclusive
    assert kz.in_watched_killzone(datetime(2026, 11, 12, 13, 59, tzinfo=SAST)) is None
    assert kz.in_watched_killzone(datetime(2026, 11, 12, 16, 59, tzinfo=SAST))["name"] == "NY AM"
    names = {k["name"] for k in kz.active_killzones(datetime(2026, 11, 12, 17, 30, tzinfo=SAST))}
    assert names == {"Silver Bullet", "London Close"}
    # Asian of the next trading day (03:00 SAST in EST) is found from the SAST clock
    assert kz.active_killzones(datetime(2026, 11, 13, 4, 0))[0]["name"] == "Asian"


def test_killzone_end_for_cancel():
    assert kz.killzone_end("NY AM", date(2026, 11, 11)).strftime("%H:%M") == "17:00"


def test_cli_exit_codes(capsys):
    assert kz.main(["at", "2026-11-12 10:00"]) == 0
    assert kz.main(["at", "2026-11-12 13:00"]) == 1
    assert kz.main(["table", "--date", "not-a-date"]) == 2
