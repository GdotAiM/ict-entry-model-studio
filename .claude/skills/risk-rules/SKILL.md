---
name: risk-rules
description: Stop buffer, minimum 1:2 R:R, TP1-partial flag, COUNTER-TREND label, and 0.5% / correlated 0.25% sizing. Load for setup-validator and whenever a stop, R multiple or size is quoted.
---
# Risk rules
See `references/risk-rules.md` for the full rules and worked numbers.
```bash
python3 .claude/skills/risk-rules/scripts/validate_setup.py --setup state/setups/N6.json --write     # exit 0 VALID, 1 NO TRADE
python3 .claude/skills/risk-rules/scripts/position_size.py --risk-pts 29 --instrument US100 --direction short --open "US100:short"
```
Never override a NO TRADE by moving the stop closer or the target further than the liquidity map supports.
