---
name: chart-markup
description: Renders the marked-up 1m chart (sweep marker, swept level, raid extreme, MSS level, FVG and OTE bands, entry, stop, targets with R) that goes out before every alert. Load for chart-annotator.
---
# Chart markup
```bash
python3 .claude/skills/chart-markup/scripts/render_chart.py --setup state/setups/N6.json --bars drafts/bars/US100_1m.csv \
    --out charts/us100_N6_1111_new.png --event NEW --last 120
```
- One PNG per alert event (`_new`, `_trig`, `_tp1`, `_tp2`, `_stop`, `_void`...). Never reuse an older chart for a new event.
- Title carries instrument, timeframe, direction, ID, event, bias label, kill zone, data source (FALLBACK if used) and the disclaimer.
- Requires matplotlib (Agg backend, no display). Example output: `examples/EXAMPLE_chart_EX1.png` (fabricated data).
