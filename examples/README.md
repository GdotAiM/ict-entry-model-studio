# examples/ — EXAMPLE DATA (FABRICATED)

Everything in this folder is **invented** to show the pipeline end to end and to drive the tests.
None of it is market data. The date (2026-01-15) and every price are made up. Never send an alert built
from these files and never quote these numbers as real levels.

| File | What it is |
|---|---|
| `make_example.py` | Regenerates the fabricated bars deterministically |
| `EXAMPLE_bars_NAS100_1m.csv` | 42 hand-built 1m bars: range, buy-side raid of 20030, displacement MSS through 20010, FVG, OTE retrace, sell-off |
| `EXAMPLE_bars_NAS100_1d.csv` | Fabricated daily bars (bear staircase; PWL 19930.0, PDL 19960.0) for the bias and the offline watch replay |
| `EXAMPLE_setup_EX1.json` | The setup record (state/SCHEMA.md) *before* validation |
| `EXAMPLE_alert_EX1_NEW.md`, `EXAMPLE_alert_EX1_TP1.md` | Alerts rendered by `render_alert.py` after `validate_setup.py --write` |
| `EXAMPLE_chart_EX1.png` | Chart rendered by `render_chart.py` |

Walkthrough (writes only to /tmp):
```bash
cp examples/EXAMPLE_setup_EX1.json /tmp/EX1.json
python3 .claude/skills/setup-detection/scripts/detect.py --csv examples/EXAMPLE_bars_NAS100_1m.csv --direction short --level 20030
python3 .claude/skills/risk-rules/scripts/validate_setup.py --setup /tmp/EX1.json --write
python3 .claude/skills/alert-format/scripts/render_alert.py --setup /tmp/EX1.json --event NEW --out /tmp/EX1_NEW.md
python3 .claude/skills/alert-format/scripts/check_alert.py --file /tmp/EX1_NEW.md --event NEW --setup /tmp/EX1.json
python3 .claude/skills/chart-markup/scripts/render_chart.py --setup /tmp/EX1.json --bars examples/EXAMPLE_bars_NAS100_1m.csv --out /tmp/EX1.png
python3 scripts/lifecycle.py --setup /tmp/EX1.json --bars examples/EXAMPLE_bars_NAS100_1m.csv
python3 .claude/skills/ict-bias/scripts/mtf_bias.py --csv examples/EXAMPLE_bars_NAS100_1d.csv
```
Full pure-Python cycle on the fabricated data (state goes to a temp folder; nothing is sent):
```bash
mkdir -p /tmp/emx/bars && cp examples/EXAMPLE_bars_NAS100_1m.csv /tmp/emx/bars/NAS100_1m.csv && cp examples/EXAMPLE_bars_NAS100_1d.csv /tmp/emx/bars/NAS100_1d.csv
python3 -c "import sys; sys.path.insert(0,'scripts'); import watch, json; from datetime import datetime as D; \
print(json.dumps(watch.run_cycle(D.fromisoformat('2026-01-15T09:26:00+02:00'), root='/tmp/emx', bars_dir='/tmp/emx/bars', dry_run=True, instruments=['NAS100'])['outbox']['dry_run'][0]['text']))"
```
