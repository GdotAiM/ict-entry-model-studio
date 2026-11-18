---
name: market-data
description: The only sanctioned way to get prices - TradingView via tvDatafeed (OANDA/TVC symbols) with a disclosed yfinance fallback. Load before any agent reads or quotes a price.
---
# Market data
| Instrument | TradingView (primary) | yfinance FALLBACK proxy |
|---|---|---|
| US100 | CAPITALCOM:US100 | NQ=F (futures, different price level!) |
| XAUUSD | OANDA:XAUUSD | GC=F (futures, premium to spot) |
| EURUSD | OANDA:EURUSD | EURUSD=X |
| GBPUSD | OANDA:GBPUSD | GBPUSD=X |
| DXY (confluence only) | TVC:DXY | DX-Y.NYB |

```bash
python3 .claude/skills/market-data/scripts/fetch_bars.py US100 1m --bars 600 --out drafts/bars/US100_1m.csv
```
- Exit 0 = TradingView. **Exit 4 = yfinance FALLBACK** — usable, but set `fallback: true` and copy `source` into the setup,
  and the alert must say "yfinance FALLBACK" (`check_alert.py` blocks it otherwise). Futures proxies are not OANDA prices; say so.
- Exit 3 = NO DATA. Stop. Never estimate, interpolate, carry forward or "remember" a price.
- 1d bars are labelled by trading date (YYYY-MM-DD; OANDA's 17:00 NY open belongs to the next day).
- Intraday times in the CSV are SAST ISO 8601. OANDA prices are **bid**.
- TradingView timeouts happen; the script retries 3x before falling back. Retry next cycle rather than chasing.
- Keep pulls small (1m ~600 bars for a kill zone; 1d ~60 bars for bias).
