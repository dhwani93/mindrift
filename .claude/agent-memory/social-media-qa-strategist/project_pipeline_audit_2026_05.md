---
name: Luna's Life Pipeline Audit - May 2026
description: Critical bugs found in the pet-POV comedy video pipeline across scriptwriter, orchestrator, seed generator, character bible, and video generation.
type: project
---

Full audit completed 2026-05-25. 7 critical, 9 medium, 6 low issues found.

Top critical bugs:
- Jade missing from NAME_TO_KEY in orchestrator.py (breaks visual prompts)
- Seed generator system prompt still describes SENIOR DOG and KITTEN (not Luna's Life characters)
- Midday venting routes Luna to vent to her BOSS (white_cat) instead of a friend
- character_bible.json lists Tiffany (S2) as Luna's best friend instead of Jade (S1)
- characters_introduced tracking uses speaker names but comparison uses character keys -- intro detection NEVER works, every episode flags every character as first appearance
- Morning topic picker shows option 6 but code only accepts 1-5
- Content policy fallback regex in seedance_video.py doesn't match actual prompt format
- **TITLE-CONTENT MISMATCH** (found 2026-05-25): YouTube title uses chosen_script.title (LLM-invented) not chosen_seed.title (user-selected). The scriptwriter LLM can echo the seed topic as its title while writing completely unrelated dialogue. Two-pronged fix needed: (1) use seed title for YouTube metadata, (2) add topic-anchoring rule to scriptwriter system prompt. ALSO: midday/evening slots don't define chosen_seed in their main branch, so switching to chosen_seed.title would crash -- need a separate episode_title variable set in all branches.

**Why:** These bugs cause broken video prompts, narratively nonsensical scenes, and perpetual "first appearance" intros.

**How to apply:** When reviewing PRs or changes to these files, verify these issues are addressed. The characters_introduced bug is especially insidious -- it silently produces subtly wrong output.
