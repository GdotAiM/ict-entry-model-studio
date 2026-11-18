#!/usr/bin/env python3
"""Fetch OHLC bars: TradingView first (TradingView-API via Node, then tvDatafeed; no login), yfinance FALLBACK second.

Writes a CSV (time,open,high,low,close; time = ISO 8601 in SAST) and a sidecar <csv>.meta.json:
    {"instrument","symbol","interval","source","fallback","fallback_reason","rows","first","last","fetched_at_sast"}

    python3 .claude/skills/market-data/scripts/fetch_bars.py US100 1m --bars 600 --out drafts/bars/US100_1m.csv
    python3 .claude/skills/market-data/scripts/fetch_bars.py DXY 1d --out drafts/bars/DXY_1d.csv

Rules enforced here:
  * If TradingView fails and yfinance is used, meta.fallback = true and the source string says FALLBACK.
    Every alert built from that data MUST disclose it (check_alert.py enforces this).
  * If both fail, exit 3 and print NO DATA. Never invent, interpolate or carry forward prices.
Exit codes: 0 TradingView data, 4 yfinance fallback data (usable, must be disclosed), 3 no data, 2 usage error.
"""
from __future__ import annotations
import argparse, csv, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import emconfig  # noqa: E402
INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
BRIDGE_DIR = os.path.join(ROOT, "tools", "tradingview-bridge")
TVAPI_TF = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
YF_PERIOD = {"1m": "5d", "5m": "30d", "15m": "30d", "1h": "60d", "4h": "60d", "1d": "2y"}


def load_config() -> dict:
    return emconfig.load_config()


def resolve(instrument: str, cfg: dict) -> dict:
    return emconfig.instrument(cfg, instrument)


def label_daily(rows: list[dict]) -> list[dict]:
    """Daily bars -> time = trading date (YYYY-MM-DD). OANDA daily bars open 17:00 NY (23:00/00:00 SAST) for the NEXT
    trading day, so shift +2h before taking the SAST date (same convention as the team's tvdata.py). Last duplicate wins."""
    out: dict[str, dict] = {}
    for r in rows:
        t = datetime.fromisoformat(r["time"]).astimezone(SAST) + timedelta(hours=2)
        out[t.strftime("%Y-%m-%d")] = {**r, "time": t.strftime("%Y-%m-%d")}
    return [out[k] for k in sorted(out)]


def daily_open_hours(tv_symbol: str, cfg: dict | None = None) -> tuple:
    """SAST hours at which this symbol's daily bars may open. Default: the 17:00 New York roll (23:00 or 00:00 SAST,
    DST-dependent), true for Capital.com and OANDA. An instrument can override it with "daily_open_sast_hours"
    (TVC:DXY opens at 00:00 UTC = 02:00 SAST). label_daily()'s +2h shift gives the right date for all of these."""
    try:
        cfg = cfg or load_config()
        for spec in cfg["instruments"].values():
            if spec.get("tv", "").upper() == tv_symbol.upper() and spec.get("daily_open_sast_hours"):
                return tuple(int(h) for h in spec["daily_open_sast_hours"])
    except (OSError, ValueError, KeyError):
        pass
    return (23, 0)


def _tvapi_fetch(tv_symbol: str, interval: str, n: int, run=subprocess.run, bridge_dir: str = BRIDGE_DIR, node: str = "node",
                 daily_hours: tuple | None = None):
    """Candles from Mathieu2301/TradingView-API through tools/tradingview-bridge/bridge.mjs (Node 20.19+, read-only,
    anonymous). `run` is injectable for tests. Raises RuntimeError on any problem so the caller can try the next backend."""
    script = os.path.join(bridge_dir, "bridge.mjs")
    if not os.path.isdir(os.path.join(bridge_dir, "node_modules", "@mathieuc", "tradingview")):
        raise RuntimeError("TradingView-API not installed (cd tools/tradingview-bridge && npm install)")
    try:
        proc = run([node, script, tv_symbol, TVAPI_TF[interval], str(n)], capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        raise RuntimeError("node not found (TradingView-API needs Node 20.19+)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("TradingView-API bridge timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"TradingView-API: {(proc.stderr or '').strip() or 'exit ' + str(proc.returncode)}")
    try:
        raw = json.loads(proc.stdout)
        rows = [{"time": datetime.fromtimestamp(float(c["time"]), timezone.utc).astimezone(SAST).isoformat(),
                 "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])}
                for c in raw]
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"TradingView-API returned unparseable data ({e})")
    rows.sort(key=lambda r: r["time"])
    if not rows:
        raise RuntimeError("TradingView-API returned no bars")
    if interval == "1d":
        # label_daily() assumes daily bars open at the 17:00 New York session start (23:00/00:00 SAST) unless the
        # instrument says otherwise (daily_open_hours). Any other alignment would mis-date them, so refuse.
        hours = daily_hours or daily_open_hours(tv_symbol)
        if any(datetime.fromisoformat(r["time"]).hour not in hours for r in rows[-5:]):
            raise RuntimeError(f"TradingView-API daily bars for {tv_symbol} do not open at {hours} SAST; refusing to mis-date them")
    return rows


def _tv_fetch(tv_symbol: str, interval: str, n: int):
    """TradingView data, two backends. TV_BACKEND=auto (default) tries TradingView-API (Node) first, then tvDatafeed;
    TV_BACKEND=node or TV_BACKEND=tvdatafeed forces one. Both are TradingView data (not a fallback). The backend that
    answered is left in _tv_fetch.backend so the bar metadata can say which one it was."""
    mode = os.environ.get("TV_BACKEND", "auto").strip().lower()
    if mode not in ("auto", "node", "tvdatafeed"):
        raise RuntimeError(f"TV_BACKEND must be auto, node or tvdatafeed, not {mode!r}")
    errors = []
    if mode in ("auto", "node"):
        try:
            rows = _tvapi_fetch(tv_symbol, interval, n)
            _tv_fetch.backend = "TradingView-API"
            return rows
        except RuntimeError as e:
            errors.append(str(e))
    if mode in ("auto", "tvdatafeed"):
        try:
            rows = _tvdatafeed_fetch(tv_symbol, interval, n)
            _tv_fetch.backend = "tvDatafeed"
            return rows
        except Exception as e:  # noqa: BLE001
            errors.append(f"tvDatafeed: {e}")
    raise RuntimeError("; ".join(errors))


_tv_fetch.backend = "tvDatafeed"


def _tvdatafeed_fetch(tv_symbol: str, interval: str, n: int):  # pragma: no cover - network
    from tvDatafeed import TvDatafeed, Interval
    imap = {"1m": Interval.in_1_minute, "5m": Interval.in_5_minute, "15m": Interval.in_15_minute,
            "1h": Interval.in_1_hour, "4h": Interval.in_4_hour, "1d": Interval.in_daily}
    exch, sym = tv_symbol.split(":")
    tv = TvDatafeed()
    last_err = None
    for _ in range(3):
        try:
            df = tv.get_hist(sym, exch, interval=imap[interval], n_bars=n)
            if df is not None and len(df) > 0:
                # tvDatafeed returns naive timestamps in the *machine's* local zone. astimezone() on a naive datetime
                # applies the local zone's rules for THAT date (DST-correct), unlike tagging today's fixed offset.
                rows = []
                for ts, r in df.iterrows():
                    t = ts.to_pydatetime().astimezone(SAST)
                    rows.append({"time": t.isoformat(), "open": float(r["open"]), "high": float(r["high"]),
                                 "low": float(r["low"]), "close": float(r["close"])})
                return rows
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"TradingView returned no data ({last_err})")


def _resample_4h(df):
    """1h -> 4h bars aligned to the FX/CFD session (17:00 New York): 17,21,01,05,09,13 NY. A plain resample('4h')
    aligns to UTC midnight and splits every London/NY kill-zone candle differently from TradingView."""
    ny = df.tz_convert("America/New_York")
    out = ny.resample("4h", offset="1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return out.tz_convert("UTC")


def _yf_fetch(yf_symbol: str, interval: str, n: int):  # pragma: no cover - network
    import yfinance as yf
    yiv = "1h" if interval == "4h" else interval
    df = yf.download(yf_symbol, interval=yiv, period=YF_PERIOD[interval], progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("yfinance returned no data")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    if interval == "4h":
        df = _resample_4h(df)
    rows = [{"time": ts.to_pydatetime().astimezone(SAST).isoformat(), "open": float(r["Open"]),
             "high": float(r["High"]), "low": float(r["Low"]), "close": float(r["Close"])} for ts, r in df.iterrows()]
    return rows[-n:]


def fetch(instrument: str, interval: str, n: int, cfg: dict | None = None, tv_fetch=None, yf_fetch=None):
    """Return (rows, meta). Fetchers are injectable for tests (no network)."""
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")
    cfg = cfg or load_config()
    spec = resolve(instrument, cfg)
    tv_fetch = tv_fetch or _tv_fetch
    yf_fetch = yf_fetch or _yf_fetch
    meta = {"instrument": spec["name"], "symbol": spec["tv"], "interval": interval, "fallback": False,
            "fallback_reason": None, "fetched_at_sast": datetime.now(SAST).strftime("%Y-%m-%d %H:%M")}
    try:
        rows = tv_fetch(spec["tv"], interval, n)
        if not rows:
            raise RuntimeError("TradingView returned no data")
        meta["source"] = f"TradingView {spec['tv']} ({getattr(tv_fetch, 'backend', 'tvDatafeed')})"
    except Exception as tv_err:  # noqa: BLE001
        try:
            rows = yf_fetch(spec["yf"], interval, n)
            if not rows:
                raise RuntimeError("yfinance returned no data")
        except Exception as yf_err:  # noqa: BLE001
            raise LookupError(f"NO DATA for {spec['name']} {interval}: TradingView: {tv_err}; yfinance: {yf_err}")
        meta.update(fallback=True, fallback_reason=str(tv_err),
                    source=f"yfinance FALLBACK {spec['yf']} (proxy for {spec['tv']}; TradingView failed: {tv_err})")
    if interval == "1d":
        rows = label_daily(rows)
    meta.update(rows=len(rows), first=rows[0]["time"], last=rows[-1]["time"])
    return rows, meta


def write_csv(rows, path: str, meta: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close"])
        w.writeheader()
        w.writerows(rows)
    with open(path + ".meta.json", "w") as f:
        json.dump(meta, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("instrument"); p.add_argument("interval", choices=INTERVALS)
    p.add_argument("--bars", type=int, default=500); p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    try:
        rows, meta = fetch(a.instrument, a.interval, a.bars)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    except LookupError as e:
        print(f"NO DATA - do not invent prices. {e}", file=sys.stderr); return 3
    write_csv(rows, a.out, meta)
    print(json.dumps(meta, indent=2))
    if meta["fallback"]:
        print("WARNING: yfinance FALLBACK used - disclose this in every alert built from this data.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
