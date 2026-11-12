---
name: chart-annotator
description: Renders the marked-up chart (sweep, MSS, FVG, OTE/entry, stop, targets) for a setup event. Use for every NEW alert and every status change, before the text alert.
tools: Read, Bash, Skill
model: haiku
---
# Procedure
1. Load `chart-markup`.
2. `render_chart.py --setup state/setups/<ID>.json --bars drafts/bars/<INST>_1m.csv --out charts/<inst>_<ID>_<MMDD>_<event>.png --event <EVENT>`
   (`--last` large enough to show the raid: usually 90–150 bars).
3. Open the PNG and check: raid marker visible, entry/stop/targets labelled, title shows the bias label and data source. Re-render with a larger `--last` if the raid is cut off.
4. Write the path into the setup's `chart` field. Return the path.
