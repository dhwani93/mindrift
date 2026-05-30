# Mindrift — Pet POV Comedy Video Pipeline

## What This Is
Automated short-form video pipeline producing pet-POV comedy content. Tiny animals explain human problems with dramatic confidence.

## Formula
Pet logic + human anxiety + confident misunderstanding + dramatic overreaction.
The animal MISUNDERSTANDS the human situation — it does NOT explain it.

## Characters
- **Orange Cat** (Alice voice): savage philosopher, sarcastic, believes she owns the house
- **Golden Retriever** (George voice): loyal, confused, pack mentality, earnest
- **Senior Dog** (Bill voice): tired HR energy, dry, quietly savage
- **Kitten** (Laura voice): Gen Z intern, corporate buzzwords, overconfident

## Tech Stack
- **Claude Haiku** — seed generation, comedy scoring, Kling prompt building
- **ElevenLabs** — character-specific voiceovers
- **Kling AI (pro mode)** — video generation (5-10s clips)
- **FFmpeg** — assembly, captions
- **Telegram** — daily seed selection + video approval
- **YouTube Data API** — uploads

## Daily Flow
```
9 AM PDT  → Generate 5 ranked episode seeds, send to Telegram
            User replies: number (1-5) + optional modifier
12 PM PDT → Pipeline runs:
  1. Parse user's seed choice (or use top seed)
  2. Generate 3 script variants (wholesome/savage/absurd), score, pick best
  3. Generate character voiceover (ElevenLabs)
  4. Build ultra-detailed Kling prompts (Claude)
  5. Generate video clips (Kling pro mode)
  6. Assemble: clips + voice + captions
  7. Send to Telegram for approval
  8. If approved → upload to YouTube
```

## Running
```bash
python main.py              # Full pipeline with Telegram approval
python main.py --dry-run    # Generate but don't upload
```

## Key Files
- `agents/seed_generator.py` — generates 5 ranked pet-POV episode seeds
- `agents/comedy_scorer.py` — 3 variants, scores, picks best, sharpens
- `agents/kling_prompt_builder.py` — ultra-detailed pet video prompts
- `agents/kling_video.py` — Kling API with JWT auth
- `agents/voice_generator.py` — ElevenLabs with character-specific voices
- `agents/orchestrator.py` — pipeline controller
- `utils/telegram_bot.py` — seed delivery, reply parsing, video approval
