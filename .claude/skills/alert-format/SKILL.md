---
name: alert-format
description: Fixed alert wording for new setups and status changes, plus the alert gate (required fields, labels, fallback disclosure, banned language). Load for alert-writer.
---
# Alert format
Template and rules: `references/alert-template.md`.
```bash
python3 .claude/skills/alert-format/scripts/render_alert.py --setup state/setups/N6.json --event NEW --out alerts/2026-11-11_N6_NEW.md
python3 .claude/skills/alert-format/scripts/check_alert.py --file alerts/2026-11-11_N6_NEW.md --event NEW --setup state/setups/N6.json
```
- Order of delivery: **chart attachment first, then the text**, one message per event.
- `check_alert.py` must exit 0. On FAIL fix the setup or the text and re-render; never send a failing alert.
- You may add ONE plain-language context sentence (e.g. "price is back near the stop"). Never remove fields.
