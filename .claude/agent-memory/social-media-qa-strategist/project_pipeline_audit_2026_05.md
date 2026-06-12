---
name: Luna's Life Pipeline Audit - May 2026 (Expanded)
description: Comprehensive audit of 3 user-reported bugs (humans in videos, missing morning seeds, stuck episode counter) plus 8 additional bugs found. 11 CRITICAL, 4 MEDIUM, 3 LOW issues total.
type: project
---

Full audit completed 2026-05-25. Three user-reported bugs confirmed with root causes. 11 critical issues total.

## Root causes of the 3 reported bugs:

1. **Humans in videos**: Jade is defined as `species: "human"` with visual `"young woman with dark hair"` in character_bible.json. `get_char_visual("jade")` returns this human description, which goes directly into Seedance prompt. No "animals only" guard anywhere. Every midday episode where Luna vents to Jade renders a human.

2. **Missing morning seeds**: Two workflows race at cron `0 16 * * *` (9AM PDT). `daily_reminder.yml` sends seeds via Telegram fire-and-forget. `daily_pipeline.yml` generates its OWN different seeds and listens for reply. User gets 2 conflicting seed lists. Reminder's seeds are never consumed by anything.

3. **Stuck at EP.1**: The REAL root cause is GitHub Actions does `actions/checkout@v4` every run, which resets `series_tracker.json` to its committed state (global_episode_count: 0). There is NO git commit+push step to persist state. Even if the counter were incremented in-memory, it's lost when the runner terminates.

## Additional critical bugs found:

- `chosen_seed` is undefined in midday/evening main branches (orchestrator.py lines 467, 473). Causes NameError crash for 2 of 3 daily slots.
- Evening cron fires at 1AM UTC (next day), so daily_story date check fails -- evening NEVER connects to morning/midday scenes.
- Content policy fallback in seedance_video.py line 63 uses `prompt` instead of `clean_prompt`, overwriting the first regex pass.
- Seed generator system prompt lists SENIOR DOG and KITTEN characters alongside "SEASON 1 ONLY" instruction -- contradictory.

**Why:** These bugs collectively mean: (a) only morning slot works, (b) no state persists between runs, (c) human characters appear in animal videos, (d) 3-scene daily arc is broken.

**How to apply:** Fixes must be in this order: (1) Add git commit/push to GH Actions, (2) redesign Jade or add animal-only guard, (3) fix chosen_seed NameError in midday/evening, (4) delete or repurpose daily_reminder.yml, (5) fix evening date mismatch.
