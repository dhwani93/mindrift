# Mindrift — AI Video Automation Pipeline

## What This Is
Automated short-form video pipeline for the "Mindrift" channel (YouTube Shorts, TikTok, Instagram Reels). Produces daily 15-60 second "what if" / mind-bending thought videos with AI-generated visuals.

## Tech Stack
- **Python 3.12+**
- **Claude API (Haiku 4.5)** — thought generation + SEO
- **ElevenLabs** — TTS voiceover (voice ID: GsfuR3Wo2BACoxELWyEF)
- **Kling AI** — AI video generation (text-to-video)
- **FFmpeg** — video stitching + audio merge
- **Telegram Bot** — daily seed input + video approval
- **YouTube Data API v3** — uploads
- **GitHub Actions** — daily cron

## Daily Flow
```
9 AM  → Telegram reminds user for thought seed
10 AM → Pipeline runs:
  1. Check Telegram for user's seed (or auto-generate)
  2. Claude generates thought + visual scene prompts
  3. ElevenLabs generates voiceover (sped up 1.18x)
  4. Kling generates video clips per scene (~5s each)
  5. FFmpeg stitches clips + merges audio
  6. Sends video to user on Telegram for approval
  7. If approved → uploads to YouTube Shorts
```

## Running
```bash
python main.py              # Full pipeline (generates + asks for approval)
python main.py --dry-run    # Generate but don't upload
```

## Configuration
- `config/settings.yaml` — all settings
- `config/.env` — API keys (gitignored)
- `data/daily_seed.txt` — manual seed input (alternative to Telegram)

## Key Files
- `agents/thought_generator.py` — Claude generates what-if thoughts + Kling prompts
- `agents/kling_video.py` — Kling AI video generation with JWT auth
- `agents/voice_generator.py` — ElevenLabs TTS + audio processing
- `agents/orchestrator.py` — pipeline controller
- `utils/telegram_bot.py` — daily reminders + approval flow
- `utils/audio_processing.py` — normalize, EQ, speed up, ambient mix
