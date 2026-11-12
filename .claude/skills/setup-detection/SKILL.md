---
name: setup-detection
description: Deterministic sweep -> MSS (with displacement, within 15 bars) -> FVG -> OTE detection, plus Unicorn (breaker + FVG) and Charter Price Action Model 1-13 checks. Load for killzone-scanner and setup-validator before calling anything a setup or naming a model.
---
# Setup detection
```bash
python3 .claude/skills/setup-detection/scripts/detect.py --csv drafts/bars/NAS100_1m.csv --direction short --level 28167.1 --since 2026-11-12T09:00
python3 .claude/skills/setup-detection/scripts/models.py --csv drafts/bars/NAS100_1m.csv --direction short --level 28167.1 --context ctx.json
```
`detect.py` stages: **not swept** → **swept** (turtle soup flag) → **mss (not valid)** (no displacement/FVG, or the break came more than
15 bars after the raid extreme) → **complete** (OTE 62/70.5/79% and an entry suggestion).

`models.py`:
- **Unicorn:** breaker block = highest up-close candle at the broken swing low (shorts; mirror for longs); Unicorn when it overlaps an FVG of the
  MSS leg inside the OTE. Entry = overlap price nearest the OTE sweet spot.
- **Charter M1–M13:** each model is a list of checks, each True / False / None (not computable). Status: detected / partial / not detected /
  insufficient data / not mechanical. See `ict-entry-models/references/charter-detection.md` for what is and isn't mechanical.
- `name_models()` gives the alert's model name (Unicorn > ICT 2022 model > core) and the best Charter model; `charter_stage()` maps status → stage.

`complete` is still only a **candidate**: the validator applies the stop buffer, 1:2 and labels.
