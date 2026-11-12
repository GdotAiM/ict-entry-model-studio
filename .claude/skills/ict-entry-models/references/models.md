# Entry models (original summaries for this project)

## Core: liquidity raid -> MSS -> FVG / OTE
1. **Raid (turtle soup):** price trades through a resting liquidity pool (old high/low, equal highs/lows, Asia range, PDH/PDL,
   session high/low) and fails to hold beyond it.
2. **MSS with displacement:** a candle *closes* through the most recent swing point that formed before the raid extreme, inside an
   energetic leg (large-bodied candles) that leaves an **FVG**. Overlapping small candles drifting through a level are not an MSS.
3. **Entry:** limit order back in the FVG and/or the **OTE** (62–79% retracement of raid extreme -> leg end; 70.5% sweet spot).
4. **Stop:** beyond the raid extreme + spread + 1m wick. **Targets:** opposite-side liquidity.
5. **Void** if TP1 trades before the fill; **invalid** if the raid extreme breaks before the fill; **cancel** at kill-zone end.

## ICT 2022 model
Same skeleton, stricter timing and location: liquidity raid in a kill zone (FX 07:00–10:00 NY; indices 08:30–11:00 NY; London Open also used),
then a fast MSS with displacement on 5m–1m, entry in the FVG of that displacement. Shorts from an FVG at/above equilibrium of the leg
(premium), longs at/below (discount). Charter **M13**.

## Unicorn
A **breaker** (the last opposing candle/swing that was raided and then broken by the MSS) overlapping an **FVG** from the same leg.
Entry in the overlap; stop beyond the breaker / raid extreme with the usual buffer. Name it "Unicorn" only when the overlap is real
(prices from the bars); otherwise it is the core model.

## Not models (never alert)
- Continuation grind to new lows/highs without a raid and reversal.
- A raid with no MSS yet (that is "watching" / Opportunity Discovery).
- An FVG that price has already fully traded through.
