---
name: ict-bias
description: Multi-timeframe bias (monthly/weekly/daily structure, 20-day dealing range premium/discount, draw on liquidity PDH/PDL, PWH/PWL, PMH/PML, old highs/lows) with confidence, invalidation and reasoning. Load for ict-bias-reader and whenever a setup needs a with-bias / COUNTER-TREND label.
---
# Multi-timeframe bias
```bash
python3 .claude/skills/ict-bias/scripts/mtf_bias.py --csv drafts/bars/EURUSD_1d.csv --dxy drafts/bars/DXY_1d.csv
python3 .claude/skills/ict-bias/scripts/mtf_bias.py --csv drafts/bars/US100_1d.csv --last 28081.0
```
Method (the full rules are in the script docstring):
| Step | What |
|---|---|
| Structure per TF | monthly, weekly (built from daily bars) and daily: swing structure (HH+HL / LH+LL) + last closed bar vs the bar before |
| Weighting | M x1, W x1.5, D x2 → score ±4.5. ≥1 = bias, ≥2.5 = "strong", else "mild" |
| Location | 20-day dealing range %. Bull at >80% / bear at <20% = EXTENDED (−0.15 confidence, "don't chase"); bull from discount / bear from premium +0.05 |
| Draw | nearest *untaken* PDH/PDL, PWH/PWL, PMH/PML, old daily swing highs/lows (60 days) on the bias side. None below/above = "open air" (new extremes) |
| DXY | EUR/GBP/XAU vs DXY inverse: confirm +0.1, disagree −0.1 and a flag (indices: none; DXY's own bias is shown on the dashboard) |
| Output | `bias`, `label`, `confidence` (0–1, low/medium/high), `invalidation` ("bias wrong on a 1H close above/below X"), `reasoning[]` |

This mirrors the team Bias Bot's file (M/W/D read, zone vs the 20d range, ranked draws, invalidation, DXY check, extended-market warnings).
The agent may override it with a written reason (e.g. a fresher team `bias.md`). A setup against the final bias is **COUNTER-TREND**.
Daily CSVs must be labelled by trading date (`fetch_bars.py ... 1d` does this). The scanner rebuilds today's forming bar from 1m bars.
