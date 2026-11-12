---
description: One kill-zone watch cycle (run every ~6 minutes in London Open and NY AM). Silent when nothing changed.
---
Run one watch cycle for $ARGUMENTS (default: now).
The deterministic path is `python3 scripts/watch.py` (add `--dry-run` to preview). Use it first; the steps below are the agent
pipeline for when Hilda wants judgement on top (e.g. a setup the script rejected).
1. `python3 .claude/skills/killzones/scripts/killzone.py now` — if exit 1 and no setup is pending/open, stop silently.
2. Use **setup-lifecycle-tracker** on all pending/open setups; for each event, **chart-annotator** then **alert-writer**.
3. If inside a watched kill zone, use **killzone-scanner**; for each complete candidate, **setup-validator**; for each VALID setup, **chart-annotator** then **alert-writer** (NEW).
4. Refresh `findings/setups.md`. If nothing changed, say nothing to Hilda.
