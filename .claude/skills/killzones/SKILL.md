---
name: killzones
description: ICT kill zones in New York time converted DST-correctly to SAST, and whether a watched kill zone is active now. Load before scanning, before quoting any session time, and to get the kill-zone end that cancels unfilled setups.
---
# Kill zones (NY time -> SAST)
| Kill zone | NY | SAST (EDT season, NY+6) | SAST (EST season, NY+7) |
|---|---|---|---|
| Asian | 20:00–00:00 (previous NY evening) | 02:00–06:00 | 03:00–07:00 |
| London Open | 02:00–05:00 | 08:00–11:00 | 09:00–12:00 |
| NY AM | 07:00–10:00 | 13:00–16:00 | 14:00–17:00 |
| Silver Bullet | 10:00–11:00 | 16:00–17:00 | 17:00–18:00 |
| London Close | 10:00–12:00 | 16:00–18:00 | 17:00–19:00 |
| NY PM | 13:30–16:00 | 19:30–22:00 | 20:30–23:00 |

South Africa has no DST; the offset changes only when the US changes (2nd Sunday of March, 1st Sunday of November).
**Never convert in your head** — run the script. Watched by default (cost-cut): London Open and NY AM.
Narrative: Asia builds the range, London raids it (Judas swing), NY delivers.

```bash
python3 .claude/skills/killzones/scripts/killzone.py table --date 2026-11-12
python3 .claude/skills/killzones/scripts/killzone.py now            # exit 0 only inside a watched kill zone
python3 .claude/skills/killzones/scripts/killzone.py at "2026-11-12 14:05"
python3 .claude/skills/killzones/scripts/killzone.py ny 07:00 10:00 --date 2026-11-12
```
