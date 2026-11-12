# ICT Charter Price Action Models — educational summary (paraphrased reference)

Five-stage plan for every model: **1 Preparation, 2 Opportunity Discovery, 3 Trade Planning, 4 Trade Execution, 5 Trade Management.**
Shared tools: IPDA look-back ranges of 20/40/60 trading days, PD arrays (FVG, order block, breaker, liquidity void), buy/sell-side liquidity,
economic calendar, kill zones. Position size = equity x risk% / stop distance (here: 0.5% risk, min 1:2).

| # | Model | Gist used by this watcher |
|---|---|---|
| M1 | Intraday scalp (15–20 pips) | Daily took a 20-day IPDA swing; trade from EQ/discount (longs) or EQ/premium (shorts) in NY 07–10; 5m OTE against London's move; stop beyond the session extreme; Mon–Wed best, no Friday; one and done |
| M2 | Short-term weekly range expansion | Weekly bias, expansion from one PD array to the opposing one, Tue–Thu at the NY open |
| M3 | Swing | Daily liquidity draws plus positioning context |
| M4 | Position (500+ pips) | 20/40/60 IPDA, seasonality, monthly/weekly bias; 4H FVG in discount/premium at London/NY open; scaled exits with a runner |
| M5 | Day-trade volatility expansion (40–50 pips) | 20-day dealing range; Tue–Thu; 15m discount/premium FVG at London/NY open; trail to BE in steps |
| M6 | Universal buy-side (market maker buy model) | Draw to a premium array; buy FVGs/voids/sell-stop runs in accumulation and re-accumulation |
| M7 | Universal sell-side | Mirror of M6 toward discount arrays |
| M8 | 25 pips a week | Last 20 days, weekly bias; 5m order-flow entry or stop raid at London/NY open at converging PD arrays |
| M9 | One shot, one kill | One high-conviction trade per setup with IPDA, liquidity and PD-array alignment |
| M10 | Swing 50–75 pips a week | Weekly expansion anchored Mon–Wed; 15m liquidity-pool raid at London/NY open toward opposing external liquidity |
| M11 | Day trade ~30 pips | Weekly expansion bias; old daily high/low as draw; entry on retrace into internal range liquidity (FVG/OB/void) |
| M12 | Scalp ~20 pips | PDH/PDL or intraday liquidity raids on 15m and below, tight risk |
| M13 | 2022 model | Liquidity raid, fast MSS with displacement and FVG on 5m–1m inside a kill zone; limit at the FVG on the right side of equilibrium |

Mapping used in alerts: 1m raid+MSS+FVG in London Open/NY AM -> **M13** (or M12 for PDH/PDL scalps); 15m pool raid with weekly expansion -> M10/M11.
Top-down always: weekly -> daily -> intraday. Limit re-entries are allowed after a stop if a new raid + MSS forms.
