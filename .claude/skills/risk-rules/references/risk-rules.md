# Risk rules (Ntloso)

## Stop
- At least **spread + a normal 1m wick beyond the raid extreme** (the true high/low of the raid, not the swept level).
- Per-instrument `stop_buffer` (`spread`, `wick_1m`) in `config/instruments.json`, **DEFAULT PLACEHOLDERS pending the Capital.com spreads**: US100 1.5 + 7.5 = 9 pts; US500 0.5 + 2.0 = 2.5 pts; XAUUSD 0.3 + 1.0; EURUSD 0.8 + 1.2 pips; GBPUSD 1.0 + 1.5 pips.
  Put real values in `config/instruments.local.json` (git-ignored, merged key by key), e.g. `{"instruments": {"US100": {"stop_buffer": {"spread": 2.0, "status": "broker X, Nov 2026"}}}}`.
  `validate_setup.py` reads them and says which source it used; alerts carry a caveat while defaults are in use.
- If the rule-compliant stop makes the best target < 2R -> **NO TRADE**.

## Reward
- Minimum **1:2**. Targets are liquidity draws (liquidity-mapper), not round R multiples.
- TP1 < 2R is a **PARTIAL only**, allowed only when a later target is >= 2R. The alert must say PARTIAL.
- Runner targets (e.g. Asia low) are informational.

## Risk per trade
| Case | Risk |
|---|---|
| Standalone | 0.5% |
| Correlated, 2–4 open in one group | 0.25% each |
| Group cap | 4 trades / 1% |
Groups: US-indices-long / US-indices-short (US100 + US500 same direction), USD-long (EUR short, GBP short), USD-short (EUR long, GBP long), gold-long / gold-short (XAUUSD, its own group). DXY is never traded.
Size = equity x risk% / (risk pts x value per point). Shown as a formula; the bot never sends orders.
A new trade after a loss in the same kill zone is allowed; the alert says the prior trade lost.

## Worked check (shape of a real Nov 2026 alert)
Short, entry 28219.0, raid extreme 28239.0, buffer 9 -> stop 28248.0, risk 29.0. TP1 28159.4 = 2.06R, TP2 28108.7 = 3.80R.
Daily bias bull -> **COUNTER-TREND**. Size: equity x 0.005 / 29.
