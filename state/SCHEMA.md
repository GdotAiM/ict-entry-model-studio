# State schema

## `state/setups/<ID>.json` — one file per setup (git-ignored)
Written by `setup-validator`, updated by `setup-lifecycle-tracker`. Example (FABRICATED): `examples/EXAMPLE_setup_EX1.json`.

| Field | Type | Notes |
|---|---|---|
| id | string | `N#` US100, `X#` XAUUSD, `E#` EURUSD, `G#` GBPUSD; never reused |
| date | `YYYY-MM-DD` | SAST date of the alert |
| instrument / symbol | string | `US100` / `CAPITALCOM:US100` |
| decimals | int | display precision (US100 1, XAU 2, FX 5) |
| timeframe | string | entry timeframe, normally `1m` |
| direction | `long`\|`short` | |
| killzone, killzone_end, killzone_end_sast | string | from `killzone.py`; `killzone_end` is ISO and drives CANCELLED |
| model | string | e.g. `Core: raid + MSS + FVG/OTE`, `ICT 2022 model`, `Unicorn` |
| charter_model / charter_stage | string | `M1`..`M13` and one of the five stages |
| daily_bias | `bull`\|`bear`\|`neutral` | from `ict-bias-reader` |
| bias_label | `with-bias`\|`COUNTER-TREND`\|`no clear bias` | written by `validate_setup.py` |
| sweep | object | `description, level, extreme, extreme_time (HH:MM), extreme_time_iso` |
| mss | object | `level, description, displacement` |
| fvg / ote | object | `{low, high}` |
| entry | number | limit price (inside FVG/OTE) |
| spread / wick | number | stop buffer used (defaults in `config/instruments.json`) |
| stop, risk_pts | number | written by `validate_setup.py` |
| targets | list | `{name, price, label?, r, partial?}`; `r` written by the validator |
| runner | object? | `{price, label}` informational, no R requirement |
| risk_pct, risk_mode, size_formula | | from `position_size.py` |
| confluence, caveats | string[] | DXY / SMT / team-file notes; stale files named here |
| data_source, fallback | string, bool | copied from the bar file's `.meta.json` |
| validation | object | `{verdict, errors, notes}` |
| status | string | `pending, filled, tp1, closed_tp1, closed_tp2, closed_stop, closed_be, void, invalid, cancelled, locked` (`locked` = found while a risk cap was hit; never alerted, terminal) |
| lock | object | only on `locked` setups: `{kind: daily|drawdown, id, reason, until}` from `lib/riskstate.py` |
| example | bool | true only for EXAMPLE (fabricated) records built by `scripts/dashboard.py --example` |
| move_to_be_after_tp1 | bool | set only when Hilda says she moved her stop to breakeven |
| alert_time, last_checked | ISO | lifecycle only replays bars after `last_checked` |
| events | list | `{event, time, price, r, note}` appended by `lifecycle.py --write` |
| chart | string | latest chart path |

## `state/alerts_log.csv` — the dedupe log (tracked)
Extends the original kill-zone alert log columns (`date,session,instrument,direction,alert_time_sast,swept,mss,zone,stop,target`) with
`setup_id, label, model, event, r, chart`. Key = (`setup_id`, `event`). **A row means the alert was delivered.**
Events: `NEW FILLED TP1 TP2 TP3 STOP BE VOID INVALID CANCELLED UPDATE LOCKED` (UPDATE dedupes on its text in `mss`).
`LOCKED` rows are account-level: `setup_id` = `RISK-DAY-YYYYMMDD` / `RISK-DD-YYYYMMDD`, `instrument` = `ALL`, reason in `mss`. `recap.py` ignores them.

## Fields added by the pure-Python scanner (`scripts/watch.py`)
| Field | Notes |
|---|---|
| unicorn | `{breaker, fvg, overlap, entry}` when a breaker block overlaps an FVG of the MSS leg (inside the OTE), else null |
| charter_model | best Charter model + status, e.g. `M13 2022 model (detected); also fits M12 (partial)` |
| charter_checks | every M1–M13 check: True / False / null (= not computable here) and a status per model |
| bias_detail | `{label, confidence, confidence_label, draw, invalidation}` from `mtf_bias.py` |
| candidate_key | `INSTRUMENT|direction|MSS break time` - stops the same shift being alerted twice |
| events[0] | `NEW` is stored as an event so the outbox can retry it if delivery failed |

## Other state
- `state/bias_YYYY-MM-DD.json` — daily cache of `mtf_bias.py` per instrument (git-ignored).
- `state/.last_slot`, `state/.watch.lock` — scheduler bookkeeping (git-ignored).
- `state/bars/<INST>_1m.csv` (+ `.meta.json`) — the closed 1m bars the last cycle used, for the dashboard charts (git-ignored).
- `state/delivery_progress.json` — parts of a not-yet-logged alert that a channel already accepted, so retries skip them (git-ignored).
- `config/preregistration.json` (optional, committed) — the pre-registered test behind the recap's UNPROVEN badge (README, Dashboard).
