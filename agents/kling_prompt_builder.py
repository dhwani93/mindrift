"""Kling Prompt Builder — generates ultra-detailed pet-POV video prompts."""

import json
import logging
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a cinematographer creating shot-by-shot prompts for Kling AI video generation. You are making short pet-POV comedy videos.

Kling renders EXACTLY what you describe. Be hyper-specific about every visual detail.

CHARACTER VISUAL REFERENCES:
- ORANGE CAT: Orange tabby cat, expressive green eyes, fluffy, sits/stands with confident posture
- GOLDEN RETRIEVER: Golden retriever, warm brown eyes, slightly tilted head, earnest expression
- SENIOR DOG: Older gray-muzzled labrador or mixed breed, tired eyes, dignified posture, lying down often
- KITTEN: Small gray/tabby kitten, huge round eyes, oversized energy, tiny but overconfident body language

VISUAL STYLE: 3D CARTOON / ANIMATED — NOT PHOTOREALISTIC.
Think Pixar, Illumination, DreamWorks style. Cute, expressive, exaggerated features.
Big eyes, round faces, smooth textures. Fun and colorful, not uncanny valley.

CHARACTER VISUAL REFERENCES:
- ORANGE CAT: Chubby round orange tabby, huge green eyes, smug expression, Garfield-meets-Puss-in-Boots energy
- GOLDEN RETRIEVER: Big fluffy golden retriever, oversized puppy eyes, dopey lovable face, always slightly worried
- SENIOR DOG: Gray-muzzled old labrador, droopy wise eyes, reading glasses optional, dignified tired energy
- KITTEN: Tiny gray tabby kitten, enormous round eyes, way too much confidence for its size

SHOT TYPES:
- Direct-to-camera stare with exaggerated expression
- Reaction shot with big cartoon eye movements
- Over-shoulder looking at a human object (laptop, bills, phone)
- Dramatic walk-away with tail swish
- Paw slam on desk/table

EVERY PROMPT MUST START WITH:
"[DURATION]-second vertical 9:16 3D animated cartoon video."

THEN INCLUDE:
1. CHARACTER: which cartoon animal, their exaggerated expression
2. SETTING: colorful cartoon room, simple clean backgrounds
3. ACTION: one clear exaggerated movement
4. CAMERA: simple movement (slow push-in, static, slight tilt)
5. STYLE: "3D animated cartoon style, Pixar-like quality, soft lighting, vibrant colors, expressive character animation. No text, no captions, no extra limbs."

GOOD PROMPT:
"5-second vertical 9:16 3D animated cartoon video. A chubby orange tabby cat with huge judgmental green eyes sits on a teal couch in a bright colorful apartment, staring directly at the camera with one eyebrow raised and a smug half-smile. The cat's arms are crossed. Warm soft cartoon lighting, clean simple background with a potted plant and window. Camera: static with very slow push-in. 3D animated cartoon style, Pixar-like quality, soft lighting, vibrant colors, expressive character animation. No text, no captions, no extra limbs."

BAD PROMPT:
"A realistic cat sitting on a couch" (wrong style, too vague)

OUTPUT FORMAT — valid JSON:
{
  "clips": [
    {
      "clip_number": 1,
      "duration_sec": 10,
      "purpose": "Hook shot — cat staring judgmentally at camera",
      "prompt": "The full ultra-detailed Kling prompt (100-200 words)"
    }
  ]
}

Generate clips that match the episode's visual direction and script beats."""


class KlingPromptBuilder:
    """Generates ultra-detailed pet-POV Kling video prompts."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=3, base_delay=2.0)
    def build_prompts(self, script: str, character: str, visual_direction: str, target_length_sec: int = 35) -> list[dict]:
        """Generate Kling prompts for a pet-POV episode.

        Args:
            script: The final sharpened script.
            character: Character type (orange_cat, golden_retriever, etc.)
            visual_direction: Brief visual direction from comedy scorer.
            target_length_sec: Total video length target.

        Returns:
            List of dicts with clip_number, duration_sec, purpose, prompt.
        """
        # Determine clip structure based on target length
        if target_length_sec <= 20:
            clip_plan = "Generate exactly 3 clips: 3 × 5-second clips = 15 seconds of generated video. The remaining ~5 seconds will use the last frame held or a slow zoom."
        elif target_length_sec <= 35:
            clip_plan = "Generate exactly 3 clips: 2 × 10-second clips + 1 × 5-second clip = 25 seconds of generated video."
        else:
            clip_plan = "Generate exactly 3 clips: 3 × 10-second clips = 30 seconds of generated video."

        user_prompt = f"""Create Kling AI video prompts for this pet-POV comedy episode.

CHARACTER: {character}
SCRIPT: "{script}"
VISUAL DIRECTION: {visual_direction}
TARGET LENGTH: {target_length_sec} seconds

{clip_plan}

Clip 1 should be the HOOK shot — the pet's expression that makes someone stop scrolling.
Clip 2 should be the REACTION/CONTEXT shot — the pet interacting with the relevant prop or situation.
Clip 3 should be the PUNCHLINE shot — the pet's final expression or dramatic action (walking away, slow blink, etc.)

Each prompt must be 100-200 words. Be EXTREMELY specific about the animal's expression, the setting, props, lighting, and camera. JSON only."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_kling_prompt", response.usage.input_tokens + response.usage.output_tokens, cost)

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(text)
        clips = data.get("clips", [])

        # Sanitize prompts for Kling API
        for clip in clips:
            prompt = clip.get("prompt", "")
            prompt = prompt.replace("[", "").replace("]", "").replace("{", "").replace("}", "")
            prompt = prompt.replace("\n", " ").replace("\r", " ")
            prompt = " ".join(prompt.split())
            if len(prompt) > 2000:
                prompt = prompt[:2000].rsplit(" ", 1)[0]
            clip["prompt"] = prompt
            logger.info(f"  Clip {clip['clip_number']}: {clip['duration_sec']}s — {clip['purpose'][:60]}")

        return clips
