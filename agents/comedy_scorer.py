"""Comedy Scorer — generates 3 script variants, scores them, picks and sharpens the best."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic
import yaml

from agents.seed_generator import EpisodeSeed
from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a comedy writer for a viral pet-POV video channel. Animals explain human problems with dramatic confidence.

THE CORE RULE: The pet MISUNDERSTANDS the human situation. It does NOT explain it.

You will receive an episode seed and must generate 3 script variants:
1. WHOLESOME — heartfelt, earnest misunderstanding, accidentally touching
2. SAVAGE — sharp, judgmental, the pet is roasting humanity
3. ABSURD — surreal, escalates to ridiculous conclusions, unhinged logic

SCRIPT RULES:
- Written as the pet's inner monologue (voiceover, not literal speech)
- First 2 seconds must hook: the funniest or most intriguing line
- Every line must earn its place — no filler, no setup without payoff
- End with a punchline or a callback to the hook
- Use the character's specific voice/personality
- Include [PAUSE 0.5s] sparingly for comedic timing
- Target word count based on length: 35s ≈ 60-80 words, 60s ≈ 110-140 words

CHARACTER VOICES:
- Orange Cat: "I" statements, possessive, contemptuous of humans, references "my house", "my couch"
- Golden Retriever: "we" statements, pack mentality, emotional, brings comfort objects to problems
- Senior Dog: dry observations, HR jargon twisted, "I have reviewed the vibes", understatement
- Kitten: corporate buzzwords misused, overconfident, "I contributed zero things and was called strategic"

SCORE EACH VARIANT (1-10) ON:
- hook_strength: Does the first line stop the scroll?
- clarity: Can someone understand the joke in 2 seconds?
- pet_pov_originality: Is this a genuinely animal perspective, not just human jokes with fur?
- punchline: Does the ending land?
- rewatchability: Would someone watch this twice?
- comment_potential: Would people tag friends or argue in comments?
- brand_safety: Could a pet brand sponsor this? (10 = totally safe, 1 = risky)

OUTPUT FORMAT — valid JSON:
{
  "variants": [
    {
      "tone": "wholesome",
      "script": "The full voiceover script with timing markers",
      "scores": {
        "hook_strength": 8,
        "clarity": 9,
        "pet_pov_originality": 7,
        "punchline": 8,
        "rewatchability": 8,
        "comment_potential": 7,
        "brand_safety": 10
      },
      "total_score": 8.1
    }
  ],
  "best_variant": 0,
  "sharpened_script": "The best variant rewritten 20% sharper — tighter wording, better punchline, stronger hook",
  "visual_direction": "Brief description of what the pet should be doing visually during key moments (for Kling prompts)",
  "caption_moments": ["SHORT PUNCHY CAPTION 1", "CAPTION 2", "PUNCHLINE CAPTION"]
}"""


@dataclass
class ScoredScript:
    script: str
    tone: str
    total_score: float
    visual_direction: str
    caption_moments: list[str]


class ComedyScorer:
    """Generates, scores, and sharpens pet-POV comedy scripts."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=3, base_delay=2.0)
    def _call_claude(self, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_comedy", response.usage.input_tokens + response.usage.output_tokens, cost)
        return response.content[0].text

    def score_and_pick(self, seed: EpisodeSeed, modifier: str = "") -> ScoredScript:
        """Generate 3 variants, score them, pick the best, sharpen it.

        Args:
            seed: The chosen episode seed.
            modifier: Optional tone modifier from user ("more savage", "more wholesome", etc.)
        """
        prompt = f"""Generate 3 script variants for this episode:

TITLE: {seed.title}
CHARACTER: {seed.character}
PREMISE: {seed.premise}
HOOK: {seed.hook}
TONE PREFERENCE: {seed.tone}
TARGET LENGTH: {seed.recommended_length_sec} seconds"""

        if modifier:
            prompt += f"\nUSER MODIFIER: {modifier}"

        prompt += "\n\nGenerate wholesome, savage, and absurd variants. Score each. Pick the best. Sharpen it. JSON only."

        response_text = self._call_claude(prompt)

        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try repair
            repaired = text
            if repaired.count('"') % 2 != 0:
                repaired += '"'
            open_brackets = repaired.count("[") - repaired.count("]")
            open_braces = repaired.count("{") - repaired.count("}")
            repaired += "]" * open_brackets
            repaired += "}" * open_braces
            data = json.loads(repaired)

        # Log all variant scores
        for v in data.get("variants", []):
            logger.info(f"  {v['tone']}: {v.get('total_score', 0)}")

        best_idx = data.get("best_variant", 0)
        best_tone = data["variants"][best_idx]["tone"] if data.get("variants") else "savage"

        result = ScoredScript(
            script=data.get("sharpened_script", data["variants"][best_idx]["script"]),
            tone=best_tone,
            total_score=data["variants"][best_idx].get("total_score", 0),
            visual_direction=data.get("visual_direction", ""),
            caption_moments=data.get("caption_moments", []),
        )

        logger.info(f"Best variant: {result.tone} ({result.total_score})")
        logger.info(f"Script: {result.script[:80]}...")
        return result
