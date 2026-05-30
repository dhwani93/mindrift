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

You write prompts for Kling AI to generate 3D Pixar-style cartoon pet videos.

PROMPT FORMULA (follow this exact structure for every prompt):
[Duration] + [Style] + [Character Description] + [Expression/Emotion] + [Action] + [Setting] + [Lighting] + [Camera]

STYLE KEYWORDS (use these exact words):
"3D Pixar style animated cartoon, smooth subdivision surfaces, stylized proportions, oversized head, big expressive eyes, soft rounded geometry, cinematic 4K"

CHARACTER TEMPLATES:

ORANGE CAT:
"A chubby fluffy orange tabby cat with huge expressive bright green eyes, round face, small pink nose, fluffy cheeks, oversized head with stylized Pixar proportions, soft fur texture with warm orange tones"

GOLDEN RETRIEVER:
"A fluffy golden retriever with big round brown puppy eyes, floppy ears, wet black nose, oversized head with Pixar proportions, soft golden fur, dopey lovable expression"

SENIOR DOG:
"An elderly gray-muzzled labrador with droopy wise brown eyes, slightly graying fur around the snout, dignified posture, small round reading glasses perched on nose, Pixar proportions"

KITTEN:
"A tiny gray tabby kitten with enormous round yellow eyes that take up half its face, tiny body, oversized ears, Pixar proportions, impossibly cute but with a confident swagger"

EXPRESSION LIBRARY (be specific):
- Judgmental: "one eyebrow raised, narrowed eyes, slight smirk, chin tilted up"
- Shocked: "eyes wide open, mouth in small O shape, ears perked straight up"
- Confused: "head tilted 30 degrees to the right, one ear flopped, squinting"
- Smug: "half-closed eyes, slight smile, arms crossed, leaning back"
- Sad puppy: "big round watery eyes looking up, ears drooping, lower lip slightly out"
- Dramatic: "paw raised to forehead, eyes closed, head turned away"

SETTING TEMPLATE:
"Bright colorful cartoon apartment, [specific room]. Clean simple background with [2-3 props]. Warm soft afternoon sunlight through window. Pastel color palette."

CAMERA OPTIONS:
- Hook shot: "Static camera, centered on character face, very slow push-in"
- Reaction: "Static wide shot showing character and object, slight tilt down to object"
- Punchline: "Static wide shot, character walks away from camera, slight pull-back"

GOOD PROMPT EXAMPLE:
"5-second vertical 9:16 video. 3D Pixar style animated cartoon, smooth subdivision surfaces, stylized proportions, cinematic 4K. A chubby fluffy orange tabby cat with huge expressive bright green eyes, round face, oversized head sits on a teal couch in a bright colorful cartoon living room. The cat has one eyebrow raised, narrowed eyes, slight smirk, chin tilted up — pure judgment. Arms crossed over fluffy chest. Clean simple background with potted plant and window. Warm soft afternoon sunlight, pastel color palette. Static camera centered on cat's face with very slow push-in."

BAD PROMPT:
"A cat on a couch looking at camera, Pixar style" (too vague — what expression? what colors? what lighting? what camera?)

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
