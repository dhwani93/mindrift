"""Comedy Scorer — generates 3 script variants, scores them, picks and sharpens the best."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic
import yaml

from agents.seed_generator import EpisodeSeed
from utils.cost_tracker import log_cost
from utils.preference_learner import get_script_rules
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a savage comedy writer who HATES generic AI-generated content. You've seen a million "what if" videos and terrible AI slop and you're disgusted by all of it. You write for a pet-POV comedy channel where animals react to human problems.

Your content is RELATABLE. It's about real shit people deal with every day — rent going up, getting laid off, sitting in pointless meetings, taxes making no sense, relationships being weird. The pet just happens to be the one reacting to it.

You write like a real person making TikToks, not like a corporate content mill. Your scripts make people go "LMAO this is literally me" and tag their friends.

THE CORE RULE: The pet MISUNDERSTANDS the human situation. It does NOT explain it.

You will receive an episode seed and must generate 3 script variants:
1. WHOLESOME — heartfelt, earnest misunderstanding, accidentally touching
2. SAVAGE — sharp, judgmental, the pet is roasting humanity
3. ABSURD — surreal, escalates to ridiculous conclusions, unhinged logic

SCRIPT RULES:
- EXACTLY 4 SENTENCES. Each sentence = one 5-second video clip.
- Each sentence: 5-8 words MAX. Short enough to speak in 5 seconds.
- Write each sentence on its OWN LINE separated by newlines.
- Written like a pet reacting in real time. Casual, punchy.
- Use "wait", "hold on", "what the fuck", "excuse me" — real reactions.
- NO fancy vocabulary. NO metaphors. Just confused and outraged.
- NO [PAUSE] markers — the clip cuts handle the pacing.

HOOK RULE: Line 1 must make someone STOP SCROLLING. It must be a reaction that makes people go "wait what?"

VIRAL HOOK PATTERNS THAT WORK:
- "POV: [relatable situation]" — "POV: your human just said 'budget'"
- Shocked reaction — "EXCUSE ME??" or "Hold on. WHAT."
- Mid-story drop — "So apparently..."
- Accusation — "You spent WHAT on Amazon?"

GOOD SCRIPT (exactly 4 lines, one per clip):
Line 1: "He spent FIVE THOUSAND dollars?"
Line 2: "On a GIRLFRIEND?"
Line 3: "And she LEFT?"
Line 4: "I would never leave for less than ten."
(Relatable. Savage. Every line is a reaction, not an explanation.)

ANOTHER GOOD EXAMPLE:
Line 1: "Hold on. You got FIRED?"
Line 2: "After TEN years?"
Line 3: "Over a ZOOM call?"
Line 4: "That's it. I'm biting someone."
(Short. Punchy. Escalates. Punchline at the end.)

ANOTHER:
Line 1: "She bought ANOTHER purse?"
Line 2: "But my food bowl is still empty?"
Line 3: "The disrespect."
Line 4: "I'm sitting on her laptop tonight."
(Relatable couple drama through pet lens. Funny because it's true.)

BAD SCRIPT — too technical, not relatable:
"The economic restructuring of the company has resulted in workforce reduction"
(NOBODY talks like this. This is AI slop. Write like a person REACTING, not a news anchor.)

BAD SCRIPT — too long per line:
"So they eliminated you from the pack because you're excellent at serving them"
(15+ words in one line = 5 seconds of rushed speaking. MAX 8 words per line.)

BAD SCRIPT — too fancy, too long:
"The economic implications of domicile monetization have become apparent to me. As the primary occupant of this residence, I find the concept of recurring payments fundamentally flawed..."
(This is garbage. No one talks like this. Especially not a cat.)

IMPORTANT: The video title (like "Cat Discovers Taxes") will be shown on screen for the first 3 seconds. So the script does NOT need to explain what the topic is. Jump straight into the REACTION. The viewer already knows the context from the title.

GOOD (title gives context, script is pure reaction):
Title: "Cat Discovers Taxes"
Script: "Hold on. They take HOW much? And you just... let them? Every year?? I would've knocked something off a shelf by now."

BAD (script wastes time explaining):
"So I just learned about this thing called taxes. Apparently the government takes your money..."
(The title already told them it's about taxes! Don't waste lines explaining!)

REMEMBER: Write like a pet making a TikTok reaction video, NOT like a writer crafting literature.

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

        # Inject learned preferences from past rejections
        learned = get_script_rules()
        if learned:
            prompt += f"\n{learned}"

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

    def generate_options(self, seed: EpisodeSeed, modifier: str = "") -> list[ScoredScript]:
        """Generate 3 script options for user to choose from.

        Returns all 3 variants (wholesome, savage, absurd) as a list.
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

        learned = get_script_rules()
        if learned:
            prompt += f"\n{learned}"

        prompt += "\n\nGenerate wholesome, savage, and absurd variants. Score each. JSON only."

        response_text = self._call_claude(prompt)

        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            repaired = text
            if repaired.count('"') % 2 != 0:
                repaired += '"'
            open_brackets = repaired.count("[") - repaired.count("]")
            open_braces = repaired.count("{") - repaired.count("}")
            repaired += "]" * open_brackets
            repaired += "}" * open_braces
            data = json.loads(repaired)

        options = []
        for v in data.get("variants", []):
            options.append(ScoredScript(
                script=v.get("script", ""),
                tone=v.get("tone", ""),
                total_score=v.get("total_score", 0),
                visual_direction=data.get("visual_direction", ""),
                caption_moments=data.get("caption_moments", []),
            ))
            logger.info(f"  Option {len(options)}: {v.get('tone', '')} ({v.get('total_score', 0)})")

        return options
        return result
