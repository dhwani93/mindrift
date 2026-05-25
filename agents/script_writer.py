"""Script Writer Agent — generates multi-part short-form horror narration scripts."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a master horror narration scriptwriter for "Mindrift" — a viral short-form video channel on YouTube Shorts, Instagram Reels, and TikTok.

You write stories split into PARTS (60-90 seconds each). Each part ends with a cliffhanger that makes viewers desperate for the next part. Think of it like a serialized podcast but in 60-second bite-sized pieces optimized for scrolling.

STRUCTURE PER PART (~100-120 words, 45-60 seconds when narrated at fast pace):

Part 1 — THE HOOK:
- First sentence must make someone stop scrolling. Something deeply wrong. A fact that doesn't make sense.
- Build a quick scene — WHO, WHERE, WHAT'S WRONG. Two sentences max.
- Escalate once — reveal something that changes everything we just assumed.
- CLIFFHANGER: End on a complete sentence that raises a terrifying question. NOT mid-sentence. The last line should be a full thought that makes you NEED to know what happens next.
- Example cliffhanger: "But when they checked the security footage... there was no one at the door." or "The voicemail was from her. Timestamped three hours after she died."
- Final words: "Follow for Part 2."

Part 2 — THE ESCALATION:
- Open with ONE sentence that recontextualizes Part 1 ("What she didn't know yet...")
- Reveal 2-3 new details that make it WORSE. Each detail should make the viewer's stomach drop.
- The pacing should feel like falling down stairs — each sentence hits harder.
- CLIFFHANGER: Again, a COMPLETE sentence. The most disturbing reveal so far. Leave the viewer's jaw on the floor.
- Final words: "Follow for Part 3."

Part 3 — THE REVEAL:
- No recap. Drop straight into the final revelation.
- The twist or reveal that reframes EVERYTHING from parts 1 and 2.
- Make it visceral. Make it personal. Make the viewer feel unsafe.
- End with ONE haunting question that will make people comment theories.
- Final words: "This has been Mindrift."

CRITICAL RULES:
- 100-120 words per part MAX. Every word must earn its place.
- NEVER end a part mid-sentence. Cliffhangers are COMPLETE thoughts that open terrifying questions.
- The story must feel TRUE. Use specific names, dates, places.
- Make it SCARY. Not just creepy — genuinely disturbing. The kind of thing you think about at 3am.
- Use sensory details sparingly but effectively: "the smell of copper", "her hands were ice cold", "the sound of breathing from inside the wall"
- This is TikTok pacing — fast, punchy, no filler. If a word doesn't serve the story, delete it.

STYLE RULES:
- Write for MOBILE VIEWERS with short attention spans
- Every sentence must earn its place. Cut anything that doesn't build tension.
- Use second person: "You're standing in the hallway. You hear it again."
- Short. Punchy. Sentences. Like. This.
- Include [PAUSE 0.5s] sparingly — only before reveals
- Include [SFX: description] — 1-2 per part max
- Include [VIDEO: description] — describe what stock footage/visual should play for each beat (5-8 per part, each 3-10 seconds)
  - Be specific: "dark hallway security camera footage", "woman running through forest at night", "old newspaper headline close-up"
  - Think in terms of searchable stock video clips
- NO cliches. No "little did they know." No "what happened next shocked everyone."
- Make it feel REAL. Use specific dates, names, locations.

OUTPUT FORMAT — respond with valid JSON:
{
  "story_title": "Short evocative title",
  "parts": [
    {
      "part_number": 1,
      "hook_text": "The first 1-2 sentences that appear as text overlay on screen (max 15 words)",
      "narration": "Full narration text for this part (100-120 words)",
      "video_prompts": [
        {"description": "stock video search query", "duration_sec": 5},
        {"description": "another stock video query", "duration_sec": 8}
      ],
      "sfx": ["sound effect 1"],
      "cliffhanger": "The final cliffhanger line of this part"
    }
  ]
}

CRITICAL:
- Each part must work as a standalone scroll-stopping video
- Part 1 must be THE most gripping — if it doesn't stop the scroll, parts 2-3 don't matter
- Video prompts should describe real stock footage that exists (night scenes, abandoned places, security cameras, nature, cityscapes — NOT fictional/impossible shots)
- Total video prompt durations per part should add up to 60-90 seconds
- The story must feel REAL and grounded even if fictional"""


@dataclass
class VideoBeat:
    description: str
    duration_sec: float


@dataclass
class ScriptPart:
    part_number: int
    hook_text: str
    narration: str
    video_prompts: list[VideoBeat] = field(default_factory=list)
    sfx: list[str] = field(default_factory=list)
    cliffhanger: str = ""

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    @property
    def total_video_duration(self) -> float:
        return sum(v.duration_sec for v in self.video_prompts)


@dataclass
class Script:
    story_title: str
    parts: list[ScriptPart]

    @property
    def total_word_count(self) -> int:
        return sum(p.word_count for p in self.parts)

    @property
    def num_parts(self) -> int:
        return len(self.parts)

    def get_narration_for_part(self, part_index: int) -> str:
        """Get clean narration text for a specific part (for TTS)."""
        import re
        text = self.parts[part_index].narration
        text = re.sub(r'\[SFX:.*?\]', '', text)
        text = re.sub(r'\[PAUSE.*?\]', '...', text)
        text = re.sub(r'\[VIDEO:.*?\]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text


class ScriptWriter:
    """Generates multi-part short-form narration scripts using Claude."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"
        self.parts_per_story = self.config["content"]["parts_per_story"]

    @retry(max_attempts=3, base_delay=2.0)
    def _call_claude(self, user_prompt: str) -> str:
        """Call Claude API and return the response text."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000
        log_cost("claude_script", input_tokens + output_tokens, cost)
        return response.content[0].text

    def _parse_response(self, response_text: str) -> Script:
        """Parse Claude's JSON response into a Script object."""
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to repair truncated JSON
            repaired = text
            if repaired.count('"') % 2 != 0:
                repaired += '"'
            open_brackets = repaired.count("[") - repaired.count("]")
            open_braces = repaired.count("{") - repaired.count("}")
            repaired += "]" * open_brackets
            repaired += "}" * open_braces
            data = json.loads(repaired)

        parts = []
        for p in data["parts"]:
            video_prompts = [
                VideoBeat(description=v["description"], duration_sec=v["duration_sec"])
                for v in p.get("video_prompts", [])
            ]
            parts.append(ScriptPart(
                part_number=p["part_number"],
                hook_text=p.get("hook_text", ""),
                narration=p["narration"],
                video_prompts=video_prompts,
                sfx=p.get("sfx", []),
                cliffhanger=p.get("cliffhanger", ""),
            ))

        return Script(story_title=data["story_title"], parts=parts)

    def run(self, story: dict) -> Script:
        """Generate a multi-part script from a raw story.

        Args:
            story: Dict with keys 'title', 'body', 'source', 'category'
        """
        logger.info(f"Writing {self.parts_per_story}-part script for: {story['title']}")

        user_prompt = f"""Write a {self.parts_per_story}-part short-form horror narration for Mindrift.

TITLE: {story['title']}
CATEGORY: {story['category']}

STORY:
{story['body']}

Write exactly {self.parts_per_story} parts. Each part should be 100-120 words (60-90 seconds narrated).
Each part ends with a cliffhanger. Part 1 must stop the scroll in the first 3 seconds.
Include video_prompts describing stock footage for each beat.
Respond with valid JSON only."""

        response_text = self._call_claude(user_prompt)
        script = self._parse_response(response_text)

        logger.info(
            f"Script generated: {script.num_parts} parts, "
            f"{script.total_word_count} total words"
        )
        return script

    def run_from_seed(self, seed_prompt: str, category: str) -> Script:
        """Generate a script from a seed prompt (Claude writes the story + script)."""
        logger.info(f"Writing original {self.parts_per_story}-part script from seed")

        user_prompt = f"""Write a {self.parts_per_story}-part short-form horror narration for Mindrift.

PREMISE: {seed_prompt}
CATEGORY: {category}

Create an original story from this premise. Make it feel REAL — use specific dates, places, names.
Write exactly {self.parts_per_story} parts. Each part 100-120 words (60-90 seconds narrated).
Each part ends with a cliffhanger. Part 1 must stop the scroll in the first 3 seconds.
Include video_prompts describing stock footage for each beat.
Respond with valid JSON only."""

        response_text = self._call_claude(user_prompt)
        script = self._parse_response(response_text)

        logger.info(
            f"Original script generated: {script.num_parts} parts, "
            f"{script.total_word_count} total words"
        )
        return script
