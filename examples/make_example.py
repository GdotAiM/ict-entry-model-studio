#!/usr/bin/env python3
"""Generates EXAMPLE (FABRICATED) 1m bars for tests and the README walkthrough.
These prices are invented to illustrate the sequence. They are NOT market data and must never be used in an alert.
    python3 examples/make_example.py  ->  examples/EXAMPLE_bars_US100_1m.csv
"""
import csv, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
START = datetime(2026, 1, 15, 9, 0, tzinfo=SAST)  # EXAMPLE date, London Open (EST season: 09:00-12:00 SAST)

# (open, high, low, close) - hand-built: range -> swing high 20030 -> swing low 20010 -> buy-side raid to 20042
# -> displacement down through 20010 leaving an FVG -> retrace into OTE -> sell-off to targets.
SEQ = [
    (20012, 20016, 20008, 20014), (20014, 20019, 20011, 20017), (20017, 20022, 20015, 20020), (20020, 20024, 20017, 20019),
    (20019, 20023, 20016, 20021), (20021, 20026, 20019, 20024), (20024, 20028, 20021, 20026), (20026, 20029, 20023, 20027),
    (20027, 20030, 20024, 20025),  # 09:08 swing high 20030 (EXAMPLE buy-side liquidity)
    (20025, 20027, 20020, 20022), (20022, 20025, 20018, 20020), (20020, 20023, 20015, 20017), (20017, 20020, 20013, 20015),
    (20015, 20018, 20010, 20012),  # 09:13 swing low 20010
    (20012, 20017, 20011, 20016), (20016, 20021, 20014, 20019), (20019, 20024, 20017, 20022), (20022, 20026, 20020, 20024),
    (20024, 20028, 20022, 20025), (20025, 20029, 20023, 20026),
    (20026, 20034, 20025, 20032),  # 09:20 trades through 20030 (sweep)
    (20032, 20042, 20030, 20033),  # 09:21 raid extreme 20042
    (20033, 20035, 20026, 20027),  # 09:22 back below 20030 (turtle soup)
    (20027, 20028, 20004, 20006),  # 09:23 displacement candle, closes below 20010 (MSS)
    (20006, 20007, 19990, 19992),  # 09:24 displacement continues -> FVG between 09:22 low 20026 and 09:24 high 20007? (see test)
    (19992, 19994, 19985, 19988),  # 09:25 leg low 19985
    (19988, 19998, 19986, 19996), (19996, 20006, 19994, 20004), (20004, 20014, 20002, 20012), (20012, 20020, 20010, 20018),
    (20018, 20027, 20016, 20021),  # 09:30 retrace into OTE (fills a sell limit up to 20027)
    (20021, 20023, 20008, 20010), (20010, 20012, 19996, 19998), (19998, 20000, 19984, 19986), (19986, 19988, 19975, 19977),
    (19977, 19980, 19968, 19970),  # 09:35 through TP1 area
    (19970, 19972, 19958, 19960), (19960, 19963, 19950, 19952), (19952, 19955, 19944, 19946), (19946, 19948, 19938, 19941),
    (19941, 19947, 19937, 19945), (19945, 19950, 19940, 19948),
]


def daily_rows():
    """EXAMPLE (FABRICATED) daily bars, trading-date labelled, 2025-10-01 .. 2026-01-14 (closed days only; the scanner
    builds the forming 2026-01-15 bar from the 1m file). A bearish staircase: lower highs / lower lows on D/W/M,
    prior-week low (PWL) 19930.0 on Fri 9 Jan, bounce, then PDL 19960.0 / PDH 20120.0 on Wed 14 Jan."""
    from datetime import date
    days, d = [], date(2025, 10, 1)
    while d <= date(2026, 1, 14):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rows, n = [], len(days)
    for i, day in enumerate(days):
        mid = 21600 - 1500 * i / (n - 1) + (60 if i % 4 in (1, 2) else -60)   # stair-step down with small swings
        o, c = mid + 20, mid - 20
        rows.append([day.isoformat(), round(o, 1), round(mid + 90, 1), round(mid - 90, 1), round(c, 1)])
    fix = {"2026-01-09": (20010.0, 20100.0, 19930.0, 19990.0), "2026-01-12": (19990.0, 20160.0, 19975.0, 20100.0),
           "2026-01-13": (20100.0, 20190.0, 20040.0, 20080.0), "2026-01-14": (20080.0, 20120.0, 19960.0, 20012.0)}
    for r in rows:
        if r[0] in fix:
            r[1:] = list(fix[r[0]])
    return rows


def main(out=os.path.join(os.path.dirname(__file__), "EXAMPLE_bars_US100_1m.csv")):
    with open(os.path.join(os.path.dirname(out), "EXAMPLE_bars_US100_1d.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close"])
        w.writerows(daily_rows())
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close"])
        for i, (o, h, l, c) in enumerate(SEQ):
            w.writerow([(START + timedelta(minutes=i)).isoformat(), o, h, l, c])
    return out


if __name__ == "__main__":
    print(main())
