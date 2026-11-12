from datetime import date, datetime, timedelta
import liquidity_levels as ll
import write_setups_md as wm
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")


def test_asia_window_dst():
    a0, a1 = ll.asia_window(date(2026, 11, 12))
    assert (a0.strftime("%H:%M"), a1.strftime("%H:%M")) == ("03:00", "07:00")
    a0, _ = ll.asia_window(date(2026, 10, 14))
    assert a0.strftime("%H:%M") == "02:00"


def test_liquidity_map_asia_and_sweep():
    # FABRICATED 1h bars across the Asian KZ (03:00-07:00 SAST in EST) then London
    t0 = datetime(2026, 11, 12, 3, 0, tzinfo=SAST)
    px = [(100, 103, 99), (101, 104, 98), (102, 105, 100), (103, 104, 101),   # Asia: high 105, low 98
          (103, 104, 102), (104, 106, 103), (105, 105.5, 97)]                  # later: takes 105 and 98
    bars = [{"time": (t0 + timedelta(hours=i)).isoformat(), "open": o, "high": h, "low": l, "close": o}
            for i, (o, h, l) in enumerate(px)]
    m = ll.liquidity_map(bars, date(2026, 11, 12))
    ah = next(x for x in m["buy_side"] if x["name"] == "Asia high")
    al_ = next(x for x in m["sell_side"] if x["name"] == "Asia low")
    assert ah["level"] == 105 and ah["swept"]["price"] == 106
    assert al_["level"] == 98 and al_["swept"]["price"] == 97


def test_setups_md_header_and_sections():
    s = {"id": "EX1", "instrument": "NAS100", "direction": "short", "entry": 1, "stop": 2, "risk_pts": 1,
         "targets": [{"name": "TP1", "price": 0, "r": 2.0}], "status": "pending"}
    txt = wm.build([s, {**s, "id": "EX0", "status": "closed_stop"}], ["EXAMPLE watch note"],
                   now=datetime(2026, 1, 15, 9, 30, tzinfo=SAST))
    lines = txt.splitlines()
    assert lines[0] == "Updated: 2026-01-15 09:30 SAST"
    assert "## Active setups" in txt and "EX1" in txt.split("## Watching")[0]
    assert "EX0" in txt.split("## Closed")[1] and "EXAMPLE watch note" in txt
