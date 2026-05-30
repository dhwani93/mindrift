# Paws & Opinions — Pet POV Comedy Video Pipeline

## What This Is
Automated short-form pet-POV comedy videos. Tiny cartoon animals react to human problems with savage confidence. Posted to YouTube Shorts (TikTok + Instagram coming).

## The Rule That Matters Most
**NO AI SLOP.** Every script, every prompt, every video must feel like a real content creator made it. If it sounds like generic AI output, it's garbage and we don't ship it.

## Comedy Formula
**Pet logic + human anxiety + confident misunderstanding + dramatic overreaction.**

The pet does NOT explain the topic. The pet MISUNDERSTANDS it.

GOOD: Cat goes "Wait. You PAY to live here? Every month?? And it goes UP??"
BAD: Cat goes "The economic implications of domicile monetization are concerning."

## Content Philosophy
- Write like a real person making TikToks, not a creative writing class
- Scripts are 20-30 words. 3-5 punchy lines. Every line must be funny or cut.
- Topics must be RELATABLE: rent, taxes, layoffs, meetings, relationships, adulting
- The humor comes from the pet's genuine confusion, not clever wordplay
- If you can't picture a real person laughing at it, don't write it

## Characters

### Orange Cat 🐱
- **Voice**: Alice (ElevenLabs ID: Xb7hH8MSUJpSbSDYk0k2)
- **Personality**: Savage, sarcastic, believes she owns everything
- **Visual**: Chubby fluffy orange tabby, huge green eyes, smug expression
- **Best for**: Rent, taxes, stocks, AI, capitalism, closed doors

### Golden Retriever 🐕
- **Voice**: George (ElevenLabs ID: JBFqnCBsd6RMkjVDRZzb)
- **Personality**: Loyal, confused, thinks everything is a pack issue
- **Visual**: Big fluffy golden, oversized puppy eyes, always worried
- **Best for**: Layoffs, return-to-office, walks, sadness, baby arrival

### Senior Dog 🐕‍🦺
- **Voice**: Bill (ElevenLabs ID: pqHfZKP75CvOlQylNhV4)
- **Personality**: Tired HR manager energy, dry, quietly savage
- **Visual**: Gray-muzzled labrador, droopy wise eyes, reading glasses
- **Best for**: Burnout, meetings, work politics, performance reviews

### Kitten 🐈
- **Voice**: Laura (ElevenLabs ID: FGY2WhTYpPnrIDTdsKH5)
- **Personality**: Gen Z intern energy, overconfident, uses buzzwords wrong
- **Visual**: Tiny gray tabby, enormous round eyes, way too much confidence
- **Best for**: Meetings, LinkedIn, tech culture, startup nonsense

## Daily Pipeline Flow
```
9 AM PDT  → Bot generates 5 ranked episode seeds → sends to Telegram
            User picks a number (1-5) + optional modifier

12 PM PDT → Pipeline runs:
  1. Parse user's seed choice
  2. Generate 3 script variants → pick best → sharpen
  3. GATE 1: Send script to Telegram → WAIT for approval
  4. Generate Kling video prompts
  5. GATE 2: Send prompts to Telegram → WAIT for approval
  6. Generate voice (ElevenLabs) + video (Kling)
  7. Merge video + audio
  8. GATE 3: Send final video to Telegram → WAIT for approval
  9. If approved → upload to YouTube
```

**3 approval gates before any upload. No credits wasted without approval.**

## Video Specs
- **Duration**: 15-20 seconds (hard cap)
- **Format**: Vertical 9:16 (1080x1920)
- **Clips**: 3 × 5-second Kling pro mode clips
- **Style**: 3D Pixar cartoon, NOT photorealistic
- **Audio**: ElevenLabs voiceover + ambient background drone
- **Voice speed**: 1.0x (natural, no speed-up)

## Kling AI Video Generation

### Prompt Formula
```
[Duration] + [Style] + [Character] + [Expression] + [Action] + [Setting] + [Lighting] + [Camera]
```

### Style Keywords (use every time)
```
3D Pixar style animated cartoon, smooth subdivision surfaces, stylized proportions, oversized head, big expressive eyes, soft rounded geometry, cinematic 4K
```

### Negative Prompt (sent with every request)
```
text, words, subtitles, captions, speech bubbles, dialogue, letters, numbers, watermark, logo, realistic, photorealistic, scary, horror, dark, ugly, distorted face, extra limbs, extra fingers, blurry, low quality, deformed, mutated, disfigured
```

### API Parameters
```json
{
  "model_name": "kling-v1",
  "prompt": "...",
  "negative_prompt": "...",
  "duration": "5",
  "aspect_ratio": "9:16",
  "mode": "pro",
  "cfg_scale": 0.7
}
```

### Expression Library
- **Judgmental**: one eyebrow raised, narrowed eyes, slight smirk, chin tilted up
- **Shocked**: eyes wide open, mouth in small O shape, ears perked straight up
- **Confused**: head tilted 30 degrees, one ear flopped, squinting
- **Smug**: half-closed eyes, slight smile, arms crossed, leaning back
- **Sad**: big round watery eyes looking up, ears drooping, lower lip out
- **Dramatic**: paw raised to forehead, eyes closed, head turned away

### Example Good Prompt
```
5-second vertical 9:16 video. 3D Pixar style animated cartoon, smooth subdivision surfaces, stylized proportions, cinematic 4K. A chubby fluffy orange tabby cat with huge expressive bright green eyes, round face, oversized head sits on a teal couch in a bright colorful cartoon living room. The cat has one eyebrow raised, narrowed eyes, slight smirk — pure judgment. Arms crossed over fluffy chest. Clean simple background with potted plant and window. Warm soft afternoon sunlight, pastel color palette. Static camera centered on cat's face with very slow push-in.
```

## Script Example

### Good Script (Cat Discovers Rent)
```
Wait. [PAUSE 0.5s]
You PAY money... to live here? [PAUSE 0.5s]
Every month?? [PAUSE 0.5s]
And it goes UP? [PAUSE 0.5s]
I've been living here for free this whole time.
```
25 words. Simple. Relatable. The cat is genuinely shocked.

### Bad Script
```
The economic implications of domicile monetization have become apparent to me. As the primary occupant of this residence, I find the recurring nature of rental payments fundamentally problematic...
```
This is AI slop. Nobody talks like this. Delete it.

## Tech Stack
- **Python 3.12+**
- **Claude Haiku 4.5** — seed generation, comedy scoring, Kling prompt building
- **ElevenLabs** — character-specific TTS voiceovers
- **Kling AI v1 (pro mode)** — 3D cartoon video generation with negative prompts
- **FFmpeg** — video stitching + audio merge
- **Telegram Bot** — seed delivery, 3-gate approval flow
- **YouTube Data API v3** — uploads
- **GitHub Actions** — daily cron (2 workflows: seeds + pipeline)

## File Structure
```
agents/
  seed_generator.py      — generates 5 ranked pet-POV episode seeds
  comedy_scorer.py       — 3 variants, scores, picks best, sharpens
  kling_prompt_builder.py — builds Pixar-style video prompts
  kling_video.py          — Kling API with JWT auth + negative prompts
  voice_generator.py      — ElevenLabs with character voices
  orchestrator.py         — 3-gate approval pipeline controller
  uploader.py             — YouTube Data API uploads
utils/
  telegram_bot.py         — seeds, reply parsing, 3 approval gates
  audio_processing.py     — normalize, EQ, ambient mix
  cost_tracker.py         — logs API spend
  retry.py                — exponential backoff
config/
  settings.yaml           — all settings, character voice configs
  .env                    — API keys (gitignored)
```

## Cost Per Video
- Claude (seeds + scoring + prompts): ~$0.03
- ElevenLabs (voiceover): ~$0.10
- Kling (3 × 5s pro clips): ~$1.12
- **Total: ~$1.25 per video**

## What NOT To Do
- Don't write scripts longer than 30 words
- Don't use fancy vocabulary — pets don't talk like professors
- Don't generate Kling videos without negative_prompt
- Don't auto-upload without 3 approvals
- Don't reuse the same seed prompt for test runs
- Don't generate 10s Kling clips when 5s gives better quality
- Don't make the pet "explain" a topic — make it REACT to discovering it
