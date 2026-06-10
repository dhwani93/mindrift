# Paws & Opinions — Luna's Life

## What This Is
Automated pet comedy universe following Luna (orange tabby cat) through her daily life — work drama, relationship chaos, home life. 3 connected scenes per day posted to YouTube Shorts.

## The Universe
One main character (Luna) with an expanding cast. Every episode is part of her story.

**Season 1 Cast:**
- **Luna** (orange tabby) — sassy protagonist, trying to hold it together
- **Milo** (golden retriever) — her boyfriend, lovable idiot
- **Ms. Whiskers** (white cat) — her terrible boss
- **Pickles** (parrot) — her pet, repeats secrets at the worst time

## Daily Flow (3 connected scenes)
```
9 AM PDT  → Scene 1: THE INCIDENT (something goes wrong)
1 PM PDT  → Scene 2: THE VENTING (Luna tells someone about it)
6 PM PDT  → Scene 3: THE AFTERMATH (resolution at home)
```
Each scene references what happened before via `data/daily_story.json`.

## Tech Stack
- **Seedance 2.0 Fast** (fal.ai) — video + voice + lip sync in one call
- **Claude Haiku** — scriptwriting, seed generation, trend scanning
- **FFmpeg** — title overlay
- **Telegram Bot** — topic selection, script approval, video approval
- **YouTube Data API** — uploads
- **GitHub Actions** — 3 daily crons (9AM/1PM/6PM PDT)

## Script Rules
- Sitcom-grade comedy: rule of three, cold opens, callbacks, misdirects
- Shareability test: tag test + quote test + sarcasm test
- Relatability over cleverness
- Day awareness: no work on weekends/holidays
- Holiday awareness: reference upcoming events
- 6-8 lines dialogue, 15 seconds, two characters

## Key Files
```
agents/
  scriptwriter.py    — sitcom dialogue writer with character bible
  seed_generator.py  — 5 daily topic seeds (work/relationship/home/trending/wildcard)
  seedance_video.py  — Seedance 2.0 via fal.ai
  orchestrator.py    — 3-scene daily pipeline
  trend_scanner.py   — hourly trending topics
  uploader.py        — YouTube uploads
data/
  character_bible.json  — full cast with personalities, catchphrases
  daily_story.json      — today's 3-scene story thread
  series_tracker.json   — global episode counter
  trending_seeds.json   — hourly trending topics
  learned_preferences.json — feedback from rejections
utils/
  telegram_bot.py     — seed delivery, approval flow
  preference_learner.py — adaptive learning from rejections
  cost_tracker.py     — API spend tracking
```

## Costs
- Seedance: ~$0.33 per 15s video
- Claude: ~$0.03 per script
- Total: ~$0.36/video, ~$1.08/day for 3 videos
