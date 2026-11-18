---
name: liquidity
description: Buy-side and sell-side liquidity map (PDH/PDL, Asia high/low, week high/low before today, equal highs/lows) with swept/untouched status. Load for liquidity-mapper and to pick sweep levels and targets.
---
# Liquidity
```bash
python3 .claude/skills/liquidity/scripts/liquidity_levels.py --csv drafts/bars/US100_5m.csv --date 2026-11-12 \
    --daily drafts/bars/US100_1d.csv --eq-tol 3.0
```
- Buy-side liquidity (BSL) rests above highs; sell-side (SSL) below lows. A **raid** of BSL sets up shorts; of SSL, longs.
- Draws on liquidity (targets) are the opposite side: the nearest untouched pools first (TP1), then the next (TP2), external range last (runner).
- Equal highs/lows (`--eq-tol` in price units: US100 ~3, US500 ~1, FX ~0.0002, XAU ~0.5, DXY ~0.02) are high-value pools.
- Levels already swept are not targets any more; levels swept *this kill zone* are sweep candidates for the scanner.
