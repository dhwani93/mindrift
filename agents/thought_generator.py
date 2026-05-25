"""Thought Generator — creates mind-bending what-if thoughts using Claude."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a viral content creator for a "What If" short-form video channel. You generate mind-bending, scroll-stopping thoughts about sci-fi, time travel, alternate history, parallel universes, and reality-breaking ideas.

Your thoughts should:
- Make someone stop scrolling and go "wait... holy shit"
- Be grounded enough to feel plausible but wild enough to blow minds
- Be 1-2 sentences MAX for the voiceover (8-12 seconds when spoken fast)
- NOT be basic shower thoughts. These need to be DEEP, specific, and VISUAL
- Focus on: hidden worlds, parallel civilizations, lost cities, what's beneath/beyond things, alternate timelines where history changed

THEME FOCUS:
- Hidden civilizations beneath real places (under the Himalayas, deep ocean, inside the Earth)
- Parallel lives — another version of you living a completely different life right now
- Ancient advanced civilizations that vanished (Wakanda-style hidden tech cities)
- Portals to other dimensions in mundane places
- What if a historical event went differently and created a wildly different present
- What exists in places humans can't go (deep ocean trenches, inside black holes, before the Big Bang)

GOOD examples:
- "What if there's a civilization beneath the Himalayas that's been watching us for 10,000 years, waiting for us to be ready"
- "There might be a version of you in a parallel universe who made every opposite choice — and they're wondering about you too"
- "What if the Sahara Desert is covering an ancient city so advanced, it makes our technology look like cave paintings"
- "What if the deepest part of the ocean has a city that's been lit up for millions of years — and we just can't get deep enough to see the lights"
- "What if every ancient temple was built on top of a portal, and the people who built them knew exactly what was underneath"

BAD examples (too generic):
- "What if we're in a simulation" (overdone, not visual)
- "What if aliens exist" (too vague)
- "What if time isn't real" (not specific, not cinematic)

OUTPUT FORMAT — respond with valid JSON:
{
  "thought": "The voiceover text (1-2 sentences for short, 2-3 for long)",
  "visual_scenes": [
    "Scene 1 prompt for Kling AI (5-10 seconds). RULES: Start with CAMERA MOVEMENT (slow dolly forward, crane shot rising, tracking shot). Describe ONE clear scene with MOTION (particles, water, light, transformation). Include LIGHTING (volumetric fog, golden hour, bioluminescent). End with STYLE (cinematic, photorealistic, 8K, anamorphic). MUST be filmable, not abstract.",
    "Scene 2 prompt — a DIFFERENT angle or the next beat of the visual story. Same rules.",
    "Scene 3 prompt (optional) — the reveal shot or final wide establishing shot."
  ],
  "hook_text": "2-5 word text overlay (the scroll-stopper)",
  "category": "time_travel|alternate_history|parallel_universe|simulation|quantum|cosmic"
}

IMPORTANT: Generate 2-3 visual_scenes that tell a visual STORY matching the thought.
- Scene 1: The establishing/approach shot
- Scene 2: The reveal/closer look
- Scene 3 (if needed): The pull-back or consequence shot
Each scene = one 5-second Kling video clip. They will be stitched together to cover the voiceover.

Generate 1 thought. Make it SPECIFIC, VISUAL, and MIND-BENDING."""


@dataclass
class Thought:
    text: str
    visual_scenes: list[str]
    hook_text: str
    category: str


class ThoughtGenerator:
    """Generates mind-bending what-if thoughts for short-form video."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=3, base_delay=2.0)
    def _call_claude(self, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_thought", response.usage.input_tokens + response.usage.output_tokens, cost)
        return response.content[0].text

    def run(self, category_hint: str = "", long_form: bool = False) -> Thought:
        """Generate a single mind-bending thought.

        Args:
            category_hint: Optional category to focus on (time_travel, alternate_history, etc.)
            long_form: If True, generate a longer 2-3 sentence thought (~25-30s spoken).
        """
        if long_form:
            prompt = "Generate a mind-bending what-if thought. Make it 2-3 sentences, more detailed and descriptive (~25-30 seconds when spoken fast)."
        else:
            prompt = "Generate a mind-bending what-if thought. Keep it to 1-2 punchy sentences (~10-15 seconds when spoken fast)."
        if category_hint:
            prompt += f" Focus on: {category_hint}."
        prompt += " Make it SPECIFIC, cinematic, and unforgettable. JSON only."

        response_text = self._call_claude(prompt)

        # Parse
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)

        # Handle both old format (visual_prompt) and new (visual_scenes)
        scenes = data.get("visual_scenes", [])
        if not scenes and data.get("visual_prompt"):
            scenes = [data["visual_prompt"]]

        thought = Thought(
            text=data["thought"],
            visual_scenes=scenes,
            hook_text=data["hook_text"],
            category=data.get("category", "cosmic"),
        )
        logger.info(f"Generated thought [{thought.category}]: {thought.text[:60]}...")
        return thought

    def run_batch(self, count: int = 5) -> list[Thought]:
        """Generate multiple thoughts for batch production."""
        categories = ["time_travel", "alternate_history", "parallel_universe", "simulation", "quantum", "cosmic"]
        thoughts = []
        for i in range(count):
            cat = categories[i % len(categories)]
            thought = self.run(category_hint=cat)
            thoughts.append(thought)
        return thoughts
