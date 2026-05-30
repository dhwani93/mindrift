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

SHOT TYPES THAT WORK FOR PET COMEDY:
- Direct-to-camera stare (pet looking into lens with judgment/confusion/concern)
- Reaction shot (pet reacting to something offscreen — head tilt, ear perk, slow blink)
- Over-shoulder (pet looking at a laptop, phone, document, or human activity)
- Walking away (pet leaving dramatically after delivering truth)
- Sitting beside object (pet next to bills, laptop, suitcase, food bowl — contextual)
- Paw on object (pet's paw resting on keyboard, document, phone)

EVERY PROMPT MUST START WITH:
"[DURATION]-second vertical 9:16 cinematic AI video."

THEN INCLUDE ALL OF THESE:
1. CHARACTER: which animal, what they look like, their expression/emotion
2. SETTING: specific room, furniture, props, time of day, lighting
3. ACTION: what the animal is doing (staring, tilting head, slow blink, walking, sitting)
4. CAMERA: movement (slow push-in, static, slight orbit, pull-back), angle (eye-level with pet, low angle, slightly above)
5. MOOD: warm home lighting, soft afternoon sun, cozy, comedic timing feel
6. SAFETY: "No text, no captions, no distorted anatomy, no extra limbs, realistic but slightly stylized social media pet video."

GOOD PROMPT:
"5-second vertical 9:16 cinematic AI video. An expressive orange tabby cat sits on a beige linen couch in a cozy sunlit apartment living room, staring directly at the camera with narrowed judgmental eyes and a subtle slow blink. Warm afternoon window light casting soft shadows, potted plant visible in background, cream-colored walls. The cat's posture is upright and regal, chin slightly raised. Camera: static with very slow push-in (barely perceptible). The cat looks like it is silently judging everything about your life choices. No text, no captions, no distorted face, no extra limbs, realistic but slightly stylized social media pet video."

BAD PROMPT:
"A cat sitting on a couch looking at the camera" (way too vague — what kind of cat? what couch? what expression? what lighting?)

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
