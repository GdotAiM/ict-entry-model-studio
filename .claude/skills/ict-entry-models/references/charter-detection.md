# Charter models: what the scanner can and cannot detect (honest table)

Source: Hilda's Charter notes (`charter-models.md`). Implemented in `.claude/skills/setup-detection/scripts/models.py`.
None = the check can't be computed from the data this project has; it neither passes nor fails a model.

| Model | Mechanical checks | Not mechanical / approximated |
|---|---|---|
| M1 Intraday scalp | daily took a 20d swing; EQ/discount (buy) or EQ/premium (sell); NY 07:00–11:00; Mon–Wed / Thu if liquidity unswept / no Fri; against London's move; OTE entry; 15–20 pip target (FX) | 5m OTE ±5 pip offset, "one and done", scaling at 15 and 20 pips |
| M2 Weekly range expansion | weekly bias; Tue–Thu; NY open; weekly expansion underway (current week took the prior week's extreme) | "opposing PD arrays" judged by the agent |
| M3 Swing | daily draw agrees | **COT commercials vs 12-month midpoint: no COT feed → always "not mechanical"** |
| M4 Position 500+ pips | — | **20/40/60 IPDA + seasonality + COT, 4H FVG, scaled exits: outside an intraday watcher → "not mechanical"** |
| M5 Day trade 40–50 pips | Tue–Thu; London/NY open; discount/premium of 20d range; 15m FVG; 40–50 pip target (FX) | the scanner works on 1m, so the 15m-FVG check normally fails → partial at best; trailing rules |
| M6 / M7 Universal buy/sell | daily draw; stop run + MSS; entry in discount/premium | accumulation/re-accumulation phases and pyramiding (<50% of run) |
| M8 25 pips/week | weekly bias; London/NY open; stop-raid entry; ~25 pip target (FX) | "PD array convergence ±5p" |
| M9 One shot one kill | bias aligned; bias confidence ≥ 0.66; raid + MSS; PD-array entry | conviction is a judgement call; limited pyramiding |
| M10 Swing 50–75/week | weekly bias; Mon–Wed; 15m pool raid; London/NY open; 50–75 pip target (FX) | 1m scanner → sweep_tf is 1m, so usually partial |
| M11 Day trade ~30 pips | weekly bias; old daily H/L as TP1; retrace into FVG/OTE; ~30 pip target (FX) | internal-range liquidity is approximated by FVG/OTE |
| M12 Scalp ~20 pips | PDH/PDL or intraday raid; 15m or lower; ~20 pip target (FX) | tight-risk judgement |
| M13 2022 model | raid; fast MSS with displacement + FVG; 5m–1m; canonical window (FX 07–10 NY, indices 08:30–11 NY); FVG on the right side of the leg EQ | London Open use is Hilda's practice: the window check is skipped (None), not failed |

Pip targets are FX-calibrated. For US100, US500 and XAUUSD those checks are None (not applied), so index and gold setups lean on M13/M12/M7/M6.
Status rule: **detected** = every computable check passes, ≥60% of checks computable, and essential checks (e.g. M13's MSS) computed and true.
