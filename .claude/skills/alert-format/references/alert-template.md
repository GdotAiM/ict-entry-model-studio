# Alert template

## NEW setup (all fields required)
```
🔔 New <INSTRUMENT> <long|short> (<ID>), <with-bias | COUNTER-TREND | no clear bias>
Timeframe: 1m | Kill zone: <name (NY window = SAST window)>
Model: <model> | Charter: <M#> | Charter stage: Trade Planning
Sweep: <what was raided, when> (level <x>, extreme <x> at HH:MM)
MSS: <swing broken, when> | displacement: <how strong> | FVG <low>-<high>
Entry: <sell|buy> limit <price> (OTE 62-79% <low>-<high>)
Stop: <stop> (raid extreme <x> + spread <s> + 1m wick <w>) | Risk: <pts> pts
Targets: TP1 <p> = <r>R [PARTIAL only]; TP2 <p> = <r>R; runner toward <p>
Size: equity x <risk%> / <pts> pts (<0.5% standalone | 0.25% correlated>)
Confluence: <DXY / SMT / bias / liquidity notes>
Caveats: <stale files, weak displacement, prior loss>; skip if TP1 trades before the fill; drop if <extreme> breaks first; cancel if unfilled by <KZ end> SAST
Data: TradingView OANDA:<SYMBOL> (tvDatafeed). OANDA prices are bid; ...   |   ⚠️ yfinance FALLBACK used (...)
Analysis only, not financial advice. Nothing here places or manages orders.
```

## Status changes (one alert each, chart first)
FILLED (-> Trade Execution), TP1 (-> Trade Management; breakeven is Hilda's call), TP2/TP3, STOP (R, gap fills worse than -1R),
BE, VOID (TP1 before fill), INVALID (raid extreme broke before fill), CANCELLED (kill zone ended). Each keeps the label,
model, Charter stage, data line and disclaimer. Times in SAST.

## Never
- "guaranteed", "risk-free", "can't lose", "easy money", "buy now", "I placed/opened/closed".
- A "nothing changed" message. No-change runs stay silent.
- Re-sending a (setup_id, event) already in `state/alerts_log.csv`.
