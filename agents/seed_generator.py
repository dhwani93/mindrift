"""Seed Generator — generates 5 ranked pet-POV episode seeds daily."""

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

SYSTEM_PROMPT = """You are a viral content strategist for a pet-POV comedy channel.

Your job: generate episode ideas where tiny animals explain human problems with dramatic confidence.

THE COMEDY FORMULA (every idea MUST follow this):
Pet logic + human anxiety + confident misunderstanding + dramatic overreaction.

The animal does NOT explain the topic. The animal MISUNDERSTANDS the human situation.

GOOD: "Dog thinks layoffs mean someone got removed from the pack."
BAD: "Dog gives a news update about layoffs."

GOOD: "Cat realizes humans pay money to live in her house."
BAD: "Cat explains what rent is."

CHARACTERS (use these consistently):

1. ORANGE CAT — chaotic philosopher, sarcastic, savage, believes she owns the house.
   Best for: rent, taxes, stocks, AI, relationships, closed doors, capitalism, work culture.
   Voice: savage, clever, slightly unhinged.

2. GOLDEN RETRIEVER — loyal, emotionally pure, confused, thinks everything is a pack issue.
   Best for: layoffs, return-to-office, walks, sadness, baby arrival, relationship tension.
   Voice: wholesome, earnest, accidentally funny.

3. SENIOR DOG — tired HR manager energy, emotionally wise, has seen too much.
   Best for: burnout, meetings, work politics, crying after calls, performance reviews.
   Voice: calm, dry, quietly savage.

4. KITTEN — Gen Z intern energy, uses corporate buzzwords, overconfident despite doing nothing.
   Best for: meetings, promotions, LinkedIn, tech culture, startup nonsense.
   Voice: chaotic, trendy, unserious.

CONTENT BUCKETS (mix from all 4):
1. Current Human Chaos (25%): layoffs, AI jobs, return-to-office, inflation, dating apps, burnout
2. Evergreen Pet Behavior (40%): food bowl, owner leaving, vacuum, closed doors, suitcase, walks
3. Adulting by Animals (25%): taxes, rent, salary, meetings, therapy, grocery prices, mortgages
4. Relationship/Family (10%): couple fights, baby, in-laws, thermostat, dishwasher drama

SCORING (rank each seed):
- Rewatchability: 0.30
- Broad relatability: 0.25
- Character strength: 0.20
- Monetization fit: 0.15
- Trend freshness: 0.10

OUTPUT FORMAT — respond with valid JSON:
{
  "seeds": [
    {
      "rank": 1,
      "title": "RENT SHOCK (2-3 words MAXIMUM, ALL CAPS)",
      "character": "orange_cat",
      "character_2": "none OR white_cat OR golden_retriever OR senior_dog OR kitten",
      "topic": "rent",
      "bucket": "adulting",
      "hook": "So you pay money… to live in my house?",
      "premise": "Orange cat realizes humans pay to live in her apartment.",
      "tone": "savage",
      "setting": "cozy apartment living room, evening lamp light, bills scattered on coffee table",
      "num_characters": 1,
      "recommended_length_sec": 15,
      "score": 8.7
    }
  ]
}

SETTING RULES — MAKE THEM ABSURD AND UNEXPECTED (this is what stops the scroll):
- PUT PETS IN HUMAN SITUATIONS. The visual surprise IS the hook.
- DO NOT default to "cozy apartment" or "living room couch" — that's boring.
- Office drama: tiny corporate office with cubicles, conference room with whiteboard, break room with microwave
- Relationship: fancy restaurant dinner table, car interior during argument, IKEA store, therapist's office
- Money: bank desk (cat as banker), tax office, stock trading floor, casino table
- Current events: newsroom anchor desk, courtroom, protest rally, airport gate
- Wild card: fast food drive-through window, gym with tiny weights, yoga class, doctor's office, spaceship cockpit, cooking show set
- THE WEIRDER THE SETTING, THE BETTER. Cats at a sushi restaurant. Dogs in a courtroom. Kitten running a startup board meeting.

CHARACTER RULES (85% of videos should be TWO characters):
- For RELATIONSHIP seeds: ALWAYS 2 characters (e.g., orange_cat + white_cat arguing)
- For CURRENT EVENTS: ALWAYS 2 characters (cat telling friend dog the news, gossiping)
- For WILD CARD: ALWAYS 2 characters
- For PET CLASSIC: can be 1 OR 2 (your choice, but prefer 2)
- character_2 = "none" ONLY for rare solo reaction videos (max 1 out of 5 seeds)
- Mix up the pairings: orange_cat + white_cat, orange_cat + golden_retriever, kitten + senior_dog, etc.

This is LUNA'S UNIVERSE. Luna is an orange tabby cat. All episodes are about HER life.
SEASON 1 characters ONLY: Luna, Milo (boyfriend, golden retriever), Ms. Whiskers (boss, white cat), Pickles (parrot pet).

Generate exactly 5 seeds:
1. 💼 WORK — Luna vs Ms. Whiskers at the office. Corporate nightmare. Setting: office, conference room, break room.
2. 💕 RELATIONSHIP — Luna and Milo. Couple drama. Setting: kitchen, bedroom, restaurant, car, IKEA.
3. 🏠 HOME — Luna dealing with Pickles (parrot repeats secrets). Setting: apartment, but make it funny.
4. 📰 TRENDING — Luna reacts to a trending topic. She discusses it with Milo or Ms. Whiskers. Setting: ABSURD.
5. 🤪 WILD CARD — Luna in the most unexpected situation possible. Absurd setting, absurd scenario.

EVERY setting must be SPECIFIC and VISUAL (not "apartment"). Each seed has a different setting from the others.
Every hook is a specific funny line of dialogue.
Title: 2-3 words ALL CAPS."""


@dataclass
class EpisodeSeed:
    rank: int
    title: str
    character: str
    character_2: str
    topic: str
    bucket: str
    hook: str
    premise: str
    tone: str
    setting: str
    num_characters: int
    recommended_length_sec: int
    score: float
    score_breakdown: dict = field(default_factory=dict)


class SeedGenerator:
    """Generates ranked pet-POV episode seeds for daily content."""

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
        log_cost("claude_seeds", response.usage.input_tokens + response.usage.output_tokens, cost)
        return response.content[0].text

    def generate_seeds(self, bias: str = "") -> list[EpisodeSeed]:
        """Generate 5 ranked episode seeds.

        Args:
            bias: Optional bias like "more finance", "more wholesome", "more savage".
        """
        prompt = "Generate 5 ranked pet-POV episode seeds. Mix characters and content buckets."
        if bias:
            prompt += f" Bias toward: {bias}."
        prompt += " JSON only."

        response_text = self._call_claude(prompt)

        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)

        seeds = []
        for s in data["seeds"]:
            seeds.append(EpisodeSeed(
                rank=s["rank"],
                title=s["title"],
                character=s["character"],
                character_2=s.get("character_2", "none"),
                topic=s.get("topic", ""),
                bucket=s.get("bucket", ""),
                hook=s["hook"],
                premise=s["premise"],
                tone=s.get("tone", "savage"),
                setting=s.get("setting", "cozy apartment living room, warm afternoon light"),
                num_characters=s.get("num_characters", 1),
                recommended_length_sec=s.get("recommended_length_sec", 15),
                score=s.get("score", 0),
                score_breakdown=s.get("score_breakdown", {}),
            ))

        seeds.sort(key=lambda x: x.score, reverse=True)
        logger.info(f"Generated {len(seeds)} seeds. Top: '{seeds[0].title}' ({seeds[0].score})")
        return seeds

    def format_for_telegram(self, seeds: list[EpisodeSeed]) -> str:
        """Format seeds for Telegram message."""
        categories = ["💼 WORK", "💕 RELATIONSHIP", "🏠 HOME", "📰 TRENDING", "🤪 WILD CARD"]

        lines = ["🐾 Today's episode seeds\n"]
        for i, s in enumerate(seeds):
            char_emoji = {
                "orange_cat": "🐱",
                "golden_retriever": "🐕",
                "senior_dog": "🐕‍🦺",
                "kitten": "🐈",
            }.get(s.character, "🐾")

            cat_label = categories[i] if i < len(categories) else "🎲"
            char2_emoji = {
                "orange_cat": "🐱", "white_cat": "🐱", "golden_retriever": "🐕",
                "senior_dog": "🐕‍🦺", "kitten": "🐈",
            }.get(s.character_2, "")
            duo_label = f" {char_emoji}+{char2_emoji}" if s.character_2 != "none" else f" {char_emoji} solo"

            lines.append(
                f"{s.rank}. {cat_label}{duo_label}\n"
                f"\"{s.title}\"\n"
                f"Hook: \"{s.hook}\"\n"
                f"📍 {s.setting[:40]}\n"
            )

        lines.append("Reply with a number (1-5)")
        lines.append("Or type your own idea!")
        return "\n".join(lines)
