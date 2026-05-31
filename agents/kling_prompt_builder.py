"""Kling Prompt Builder — builds concise, high-quality Pixar-style pet video prompts.

Research-backed rules (from Kling AI docs, community guides, creator blogs):
1. Prompts under 40-50 words work BEST. Long prompts confuse the model.
2. Use "warm 3D storybook cartoon" not just "Pixar" — stays cuter.
3. Formula: Camera + Subject + Action + Setting + Lighting (in that order).
4. Negative prompts must be SPECIFIC (not generic "no bad things").
5. Keep clips 5 seconds — less drift, higher quality.
6. Anchor hands to objects — never floating in empty space.
7. One clear action per clip — don't cram multiple movements.

Sources:
- https://klingaio.com/blogs/kling-3-prompt-guide
- https://filmora.wondershare.com/ai-prompt/kling-ai-prompt-guide.html
- https://www.neolemon.com/blog/kling-ai-grok-ai-character-consistency-tips/
- https://ai-pro.org/learn-ai/articles/the-kling-ai-pet-video-trend-creators-are-using-now
- https://www.banana-prompts.net/25-easy-prompts-for-3d-cartoon/
"""

import json
import logging
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.preference_learner import get_prompt_rules
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

# Character templates — locked visual identity for consistency
CHARACTERS = {
    "orange_cat": "chubby fluffy orange tabby cat, huge bright green eyes, round face, small pink nose, oversized head",
    "golden_retriever": "fluffy golden retriever, big round brown puppy eyes, floppy ears, wet black nose, oversized head",
    "senior_dog": "elderly gray-muzzled labrador, droopy wise brown eyes, small round reading glasses on nose",
    "kitten": "tiny gray tabby kitten, enormous round yellow eyes taking up half its face, tiny body, oversized ears",
}

# Expression keywords that Kling responds to
EXPRESSIONS = {
    "judgmental": "one eyebrow raised, narrowed eyes, slight smirk, chin up",
    "shocked": "eyes wide open, mouth small O shape, ears straight up",
    "confused": "head tilted right, one ear flopped, squinting",
    "smug": "half-closed eyes, slight smile, leaning back",
    "sad": "big watery eyes looking up, ears drooping",
    "dramatic": "paw on forehead, eyes closed, head turned away",
    "disgusted": "nose scrunched, one eye squinting, leaning away",
    "suspicious": "eyes narrowed to slits, ears back, chin low",
}

# Negative prompt — specific artifacts to block
NEGATIVE_PROMPT = (
    "text, words, subtitles, captions, speech bubbles, letters, numbers, watermark, logo, "
    "realistic, photorealistic, scary, horror, dark, "
    "face morphing, outfit change, hair color change, "
    "extra limbs, extra fingers, floating hands, "
    "blurry, low quality, deformed, disfigured, plastic skin"
)

SYSTEM_PROMPT = """You write SHORT, PRECISE prompts for Kling AI video generation.

CRITICAL RULE: Keep each prompt under 40 words. Kling works WORSE with long prompts.

FORMULA (this exact order):
Camera movement + Character + Expression + Action + Setting + Style tag

STYLE TAG (end every prompt with):
"warm 3D storybook cartoon, soft lighting, cinematic 4K"

CHARACTER TEMPLATES (use these exact words):
- ORANGE CAT: "chubby fluffy orange tabby cat, huge bright green eyes, round face, oversized head"
- GOLDEN RETRIEVER: "fluffy golden retriever, big round brown puppy eyes, floppy ears, oversized head"
- SENIOR DOG: "elderly gray-muzzled labrador, droopy wise brown eyes, reading glasses on nose"
- KITTEN: "tiny gray tabby kitten, enormous round yellow eyes, tiny body, oversized ears"

EXPRESSION KEYWORDS:
- Judgmental: "one eyebrow raised, narrowed eyes, slight smirk"
- Shocked: "eyes wide open, mouth O shape, ears straight up"
- Confused: "head tilted, one ear flopped, squinting"
- Smug: "half-closed eyes, slight smile, leaning back"

AESTHETIC RULES (what makes viral pet content LOOK good):
- Warm cozy lighting. Soft sunset tones. Golden hour feel. NOT sterile or clinical.
- Lived-in environment. Books, plants, mugs, blankets, warm wood. NOT empty rooms.
- Shallow depth of field. Background softly blurred. Character sharp.
- Emotional expressions. Big eyes that FEEL something. Not just "staring."
- Subtle fur movement. Gentle breathing. Ear twitch. NOT static.

GOOD PROMPT (40 words):
"Static medium shot. Cozy apartment living room, warm sunset light through window, bookshelf and plants in soft focus behind. Chubby orange tabby cat sits on worn sofa, one eyebrow raised, arms crossed, judging. Soft fur movement, gentle breathing. Warm 3D storybook cartoon, cinematic 4K."

BAD PROMPT:
"3D Pixar style. Cat on couch. Bright colors. Staring at camera."
(Sterile. No warmth. No environment. No emotion. No life.)

CAMERA OPTIONS (pick ONE per clip):
- "Static medium shot, character visible from waist up with room visible around them." (hook shot — shows character AND environment)
- "Static wide shot showing full room with character sitting in it." (context shot — establishes the world)
- "Slight pull-back revealing full room." (reveal shot — shows aftermath)
- "Low angle looking up, room ceiling visible." (power shot — character dominance)

FRAMING RULE: NEVER do extreme close-up on just the face. ALWAYS show at least waist-up AND the room/environment around the character. The setting is part of the comedy — a cat sitting on a couch in a real apartment is funnier than a floating cat head.

ONE ACTION PER CLIP. Do not combine multiple movements.

OUTPUT FORMAT — valid JSON:
{
  "clips": [
    {
      "clip_number": 1,
      "duration_sec": 5,
      "purpose": "Hook — cat judging camera",
      "prompt": "The 35-40 word prompt"
    }
  ]
}

Generate exactly 3 clips. Each prompt MUST be under 40 words."""


class KlingPromptBuilder:
    """Builds concise, research-backed Kling video prompts for pet-POV content."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=3, base_delay=2.0)
    def build_prompts(self, script: str, character: str, visual_direction: str, target_length_sec: int = 20) -> list[dict]:
        """Generate 3 concise Kling prompts for a pet-POV episode."""

        char_desc = CHARACTERS.get(character, CHARACTERS["orange_cat"])

        user_prompt = f"""Create 3 Kling video prompts for this pet comedy episode.

CHARACTER: {char_desc}
SCRIPT: "{script}"
VISUAL DIRECTION: {visual_direction}

Clip 1 = HOOK: Medium shot — character in cozy warm room, soft sunset lighting, expressive face, room feels lived-in with books/plants/blankets.
Clip 2 = REACTION: Character looking at or interacting with a prop, warm lighting, shallow depth of field, environment visible.
Clip 3 = PUNCHLINE: Character's final reaction — walking away or dramatic gesture, warm cozy room around them, emotional.

Each prompt MUST be under 45 words. Use the formula: Camera + Setting (cozy, warm, lived-in) + Character + Expression + Action + "warm 3D storybook cartoon, soft sunset lighting, shallow depth of field, cinematic 4K"

Make it feel WARM and COZY. Like a Ghibli film meets a TikTok pet video. NOT sterile or clinical.

JSON only. Exactly 3 clips, each 5 seconds.
{get_prompt_rules()}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
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

        # Sanitize and validate
        for clip in clips:
            prompt = clip.get("prompt", "")
            # Remove brackets and normalize
            prompt = prompt.replace("[", "").replace("]", "").replace("{", "").replace("}", "")
            prompt = prompt.replace("\n", " ").replace("\r", " ")
            prompt = " ".join(prompt.split())
            # Cap at 300 chars (roughly 40-50 words)
            if len(prompt) > 400:
                prompt = prompt[:300].rsplit(" ", 1)[0]
                if "cartoon" not in prompt[-50:]:
                    prompt += " Warm 3D storybook cartoon, cinematic 4K."
            clip["prompt"] = prompt
            clip["duration_sec"] = 5  # Force 5 seconds

            word_count = len(prompt.split())
            logger.info(f"  Clip {clip['clip_number']}: {word_count} words — {clip.get('purpose', '')[:50]}")
            if word_count > 50:
                logger.warning(f"    ⚠️ Prompt too long ({word_count} words) — may reduce quality")

        return clips

    @staticmethod
    def get_negative_prompt() -> str:
        """Return the standard negative prompt for Kling API calls."""
        return NEGATIVE_PROMPT
