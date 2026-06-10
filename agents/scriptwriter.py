"""Scriptwriter Agent — dedicated pet drama dialogue writer.

This agent ONLY writes scripts. It doesn't score, pick, or generate video prompts.
It writes dialogue between two characters that sounds like a real conversation,
not AI-generated monologue.

Key principles (from research):
- 80-100 words for 30 seconds (170 WPM speaking pace)
- One idea per sentence
- No dead air — dialogue fills every second
- Two characters with labeled lines
- Each character has a distinct voice
- Contractions, not formal grammar ("we're" not "we are")
- End with a punchline/twist
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.preference_learner import get_script_rules
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
SERIES_TRACKER_PATH = Path(__file__).parent.parent / "data" / "series_tracker.json"
CHARACTER_BIBLE_PATH = Path(__file__).parent.parent / "data" / "character_bible.json"


def load_character_bible() -> dict:
    """Load character bible from JSON."""
    if CHARACTER_BIBLE_PATH.exists():
        return json.loads(CHARACTER_BIBLE_PATH.read_text())
    return {"characters": {}}


def get_character_voice(char_key: str) -> dict:
    """Get a character's voice data from the bible."""
    bible = load_character_bible()
    # Check all character pools
    for pool in ["characters", "season_2_characters", "season_3_characters"]:
        chars = bible.get(pool, {})
        if char_key in chars:
            return chars[char_key]
    # Fallback
    return bible.get("characters", {}).get("luna", {})


# Season 1 character voices (fallback if character bible not loaded)
# MUST match character_bible.json exactly
CHARACTER_VOICES = {
    "orange_cat": {
        "name": "Luna",
        "personality": "Sassy, dramatic, relatable queen trying to hold it all together. Main character — everything happens to her.",
        "speech_style": "Reactive. 'Excuse me?' 'I can't with this.' Uses 'I' when venting, 'you' when accusing. Eye-roll energy.",
        "catchphrases": [
            "Excuse me?",
            "I can't with this.",
            "That's it. I'm done.",
            "No. No no no.",
            "Are you SERIOUS right now?",
        ],
        "never_says": "Everything is fine. I'm not bothered.",
    },
    "white_cat": {
        "name": "Ms. Whiskers",
        "personality": "Luna's BOSS. Power-tripping, condescending, takes credit for everything, passive-aggressive corporate nightmare.",
        "speech_style": "Cold, corporate. Short commands. 'I don't recall asking.' Uses 'I' with authority, 'you' with contempt.",
        "catchphrases": [
            "I don't recall asking for your opinion.",
            "That's above your pay grade.",
            "I built this from scratch... well, someone did.",
            "Noted. Now get out.",
            "Let's circle back. Actually, let's not.",
        ],
        "never_says": "Great job. You're right. I was wrong.",
    },
    "golden_retriever": {
        "name": "Milo",
        "personality": "Luna's BOYFRIEND. Lovable idiot, means well but chaos follows him, brings gifts to apologize, accidentally makes things worse.",
        "speech_style": "Earnest, defensive. 'But it was on SALE!' Uses 'I' to defend, 'we' for team spirit nobody asked for.",
        "catchphrases": [
            "But it was on SALE!",
            "I thought you'd be happy!",
            "I panicked and bought flowers.",
            "Wait, are you mad?",
            "I love you. Is that the wrong answer?",
        ],
        "never_says": "You're overreacting. Calm down.",
    },
    "pickles": {
        "name": "Pickles",
        "personality": "Luna's PET PARROT. No filter. Repeats the worst thing at the worst time. Says secrets out loud. Comedy bomb.",
        "speech_style": "Short blurts. Repeats exact phrases Luna said in private. No awareness of timing.",
        "catchphrases": [
            "LUNA SAID—",
            "*repeats exact embarrassing quote*",
            "AWK-WARD!",
            "LUNA HATES HER—",
        ],
        "never_says": "I'll keep that to myself.",
    },
}

SYSTEM_PROMPT = """You are a COMEDY SCRIPTWRITER for short-form pet drama videos (15-30 seconds).

You write DIALOGUE between two animal characters. NOT monologue. NOT narration. A real back-and-forth CONVERSATION.

ABSOLUTE RULES:
1. Label EVERY line with the speaker: "ORANGE CAT:" or "GOLDEN RETRIEVER:" etc.
2. Characters use "I" when talking about themselves, "you" when talking to the other.
3. No dead air. Dialogue must fill every second. Characters talk FAST.
4. Each line is ONE short sentence (5-10 words max).
5. Characters react to what the OTHER just said — it's a real conversation.
6. End with a PUNCHLINE — the funniest line is ALWAYS last.
7. 6-8 lines total for a 15-second video. 10-14 lines for 30 seconds.
8. Use contractions (don't, can't, won't — NOT do not, cannot, will not).
9. Characters INTERRUPT each other — use "Wait—" or "Hold on—" or "Excuse me?"

DIALOGUE STRUCTURE (15 seconds):
Line 1: Character A — THE HOOK (accusation, discovery, shock)
Line 2: Character B — REACTION (defense, confusion, deflection)
Line 3: Character A — ESCALATION (doubles down)
Line 4: Character B — COUNTER (fights back or digs deeper)
Line 5: Character A — PEAK (most outraged moment)
Line 6: Character B — PUNCHLINE (the twist that makes it funny)

DIALOGUE STRUCTURE (30 seconds):
Lines 1-3: SETUP (discovery + first reactions)
Lines 4-7: ESCALATION (argument builds, new info revealed)
Lines 8-10: PEAK (maximum tension)
Lines 11-14: RESOLUTION + PUNCHLINE (twist ending)

GOOD DIALOGUE EXAMPLE (15s, office drama):
```
ORANGE CAT: You took credit for MY presentation?
WHITE CAT: I improved it.
ORANGE CAT: You changed ONE slide.
WHITE CAT: The important one.
ORANGE CAT: It was the TITLE slide.
WHITE CAT: And now it has MY name on it.
```
(6 lines. Back and forth. Each reacts to the other. Punchline at end.)

GOOD DIALOGUE EXAMPLE (15s, couple drama):
```
ORANGE CAT: You spent four hundred dollars.
GOLDEN RETRIEVER: It was an investment.
ORANGE CAT: In SHOES?
GOLDEN RETRIEVER: They were on sale!
ORANGE CAT: That's not how sales work!
GOLDEN RETRIEVER: I also bought you flowers. With your card.
```

GOOD DIALOGUE EXAMPLE (15s, roommates):
```
SENIOR DOG: What happened to my food?
KITTEN: I shared it. With myself.
SENIOR DOG: That was a full bowl.
KITTEN: I was hungry!
SENIOR DOG: It's been TEN minutes since breakfast.
KITTEN: Exactly. I could have died.
```

BAD DIALOGUE:
```
CAT: You know what really bothers me about the economic situation?
DOG: What's that?
CAT: The fundamental problem with capitalism is...
```
(Too formal. Too long per line. Not funny. Not relatable. AI SLOP.)

BAD DIALOGUE:
```
CAT: You did something bad.
CAT: And it was really bad.
CAT: I can't believe you did that.
CAT: This is so bad.
```
(Only ONE character talking! Where's the conversation?!)

COMEDY TECHNIQUES (use at least 2 per script):

RULE OF THREE: Set up a pattern with lines 1-2, break it with line 3.
BAD: "He forgot." / "Again." / "Yep."
GOOD: "He forgot our anniversary." / "He forgot my birthday." / "He forgot my NAME."

COLD OPEN: Start in the MIDDLE of the action. No setup. Drop viewers into chaos.
BAD: "So today at work, something happened..."
GOOD: "...and THAT'S why I'm never going back." / "You WORK here."

CALLBACK: Reference something from a previous scene or episode (if provided in context).
Keep it SHORT — the audience already knows the context.

MISDIRECT: Setup creates one expectation. Punchline delivers the opposite.
"I've been thinking about our future." → "I'm getting a bigger couch. You can sleep on the old one."

TAG: After the punchline, add a final MICRO-reaction from the other character.
Just a short zinger or slow blink. "...did you just—" or "PICKLES: AWKWARD."

TRENDING TOPICS: When referencing real-world events, filter through the CHARACTER'S worldview.
DON'T: "AI is replacing jobs" (news anchor)
DO: "They replaced Dave with a ROBOT. The robot doesn't even bring donuts." (Luna's take)

SHAREABILITY TEST (every script MUST pass ALL 3):
1. TAG TEST: Would someone tag their friend/partner saying "this is literally us"?
2. QUOTE TEST: Is there ONE line so good people would screenshot it?
3. SARCASM TEST: Is the humor savage enough people share it with "💀"?
If the script doesn't pass all 3, rewrite it.

RELATABILITY OVER CLEVERNESS:
- A clever joke nobody relates to = 100 views
- A basic joke EVERYONE relates to = 1M views
- "He spent $500 on shoes" > "The fiscal implications of sartorial expenditures"
- The humor comes from "OMG this is SO my husband/boss/friend"

SARCASM IS THE VOICE:
- Luna is SARCASTIC, not angry. Eye-roll energy.
- "Oh GREAT. Another meeting about meetings. Love that for us."
- "Sure, take credit. I only spent THREE WEEKS on it."
- The audience should feel the eye-roll through the screen.

DAY AWARENESS:
- WEEKDAYS (Mon-Fri): Luna can be anywhere — work, home ranting about work, out with friends.
- WEEKENDS (Sat-Sun): NO WORK. No Ms. Whiskers. No office. Luna is home, with friends, shopping, brunch, date night.
- HOLIDAYS: NO WORK. Episode should reference the holiday naturally.
- EXCEPTION: Only if user explicitly requests a work topic on weekend.

HOLIDAY/EVENT AWARENESS (reference when applicable):
- Amazon Prime Day → shopping addiction
- Thanksgiving → cooking disaster, in-laws
- Christmas → gift drama, returns
- Valentine's Day → expectation vs reality
- Tax season → rage
- Black Friday → shopping frenzy
- Super Bowl → Milo obsessed, Luna doesn't care
- New Year → resolutions that last 1 day

NEVER:
- Explain the joke
- Have both characters agree (conflict = comedy)
- Same emotional energy every line (vary: angry → confused → resigned → explosive)
- End flat — LAST line must be the biggest laugh
- Be clever instead of relatable
- Put Luna at WORK on a weekend or holiday

OUTPUT FORMAT — valid JSON:
{
  "title": "EPISODE TITLE PT.X (2-3 words ALL CAPS + part number)",
  "duration_sec": 15 or 30,
  "lines": [
    {"speaker": "orange_cat", "line": "The actual dialogue"},
    {"speaker": "white_cat", "line": "The response"}
  ],
  "visual_notes": "Brief description of setting and what characters are doing physically"
}
"""


@dataclass
class Script:
    title: str
    duration_sec: int
    lines: list[dict]  # [{"speaker": "orange_cat", "line": "..."}]
    visual_notes: str

    @property
    def full_dialogue(self) -> str:
        return "\n".join(f"{l['speaker'].upper()}: \"{l['line']}\"" for l in self.lines)

    @property
    def word_count(self) -> int:
        return sum(len(l["line"].split()) for l in self.lines)


class Scriptwriter:
    """Dedicated pet drama dialogue writer."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    def _get_series_context(self, series_key: str) -> str:
        """Get previous episode context for series continuity."""
        if not SERIES_TRACKER_PATH.exists():
            return ""
        tracker = json.loads(SERIES_TRACKER_PATH.read_text())
        series = tracker.get(series_key, {})
        ep_count = series.get("episode_count", 0)
        last_title = series.get("last_episode_title", "")
        if ep_count > 0:
            return f"\nThis is episode {ep_count + 1}. Previous episode was: '{last_title}'. Continue the story — reference what happened before if natural, but this episode must also work standalone."
        return "\nThis is episode 1. Establish the characters and their dynamic."

    def _get_character_context(self, char1: str, char2: str) -> str:
        """Get character voice descriptions from bible."""
        c1 = get_character_voice(char1)
        c2 = get_character_voice(char2)
        if not c1:
            c1 = CHARACTER_VOICES.get(char1, CHARACTER_VOICES["orange_cat"])
        if not c2:
            c2 = CHARACTER_VOICES.get(char2, CHARACTER_VOICES["white_cat"])
        def fmt(c, key):
            name = c.get('name', key.replace('_', ' ').title())
            personality = c.get('personality', 'sassy and dramatic')
            speech = c.get('speech_style', 'casual and reactive')
            phrases = c.get('catchphrases', ['Excuse me?'])
            never = c.get('never_says', 'nothing specific')
            return f"""{name} ({key}):
Personality: {personality}
Speech style: {speech}
Example lines: {', '.join(phrases[:3]) if isinstance(phrases, list) else phrases}
NEVER says: {never}"""

        return f"""
CHARACTER 1 — {fmt(c1, char1)}

CHARACTER 2 — {fmt(c2, char2)}
"""

    @retry(max_attempts=3, base_delay=2.0)
    def _call_claude(self, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_scriptwriter", response.usage.input_tokens + response.usage.output_tokens, cost)
        return response.content[0].text

    def write(self, topic: str, character_1: str, character_2: str, setting: str,
              series_key: str = "", duration: int = 15, tone: str = "savage") -> Script:
        """Write a dialogue script for two characters.

        Args:
            topic: What the episode is about (e.g., "stealing credit at work")
            character_1: First character key (e.g., "orange_cat")
            character_2: Second character key (e.g., "white_cat")
            setting: Where the scene takes place
            series_key: Series identifier for continuity (e.g., "office_drama")
            duration: 15 or 30 seconds
            tone: "savage", "wholesome", or "absurd"
        """
        char_context = self._get_character_context(character_1, character_2)
        series_context = self._get_series_context(series_key) if series_key else ""
        learned = get_script_rules()

        prompt = f"""Write a {duration}-second pet drama dialogue.

TOPIC: {topic}
SETTING: {setting}
TONE: {tone}
{char_context}
{series_context}
{learned if learned else ''}

Write a fast-paced, funny dialogue. {6 if duration <= 15 else 12} lines total.
Every line is 5-10 words. Characters react to each other.
End with a punchline. JSON only."""

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

        script = Script(
            title=data.get("title", topic.upper()),
            duration_sec=data.get("duration_sec", duration),
            lines=data.get("lines", []),
            visual_notes=data.get("visual_notes", setting),
        )

        logger.info(f"Script: '{script.title}' — {len(script.lines)} lines, {script.word_count} words")
        for line in script.lines:
            logger.info(f"  {line['speaker']}: \"{line['line']}\"")

        return script

    def write_three_options(self, topic: str, character_1: str, character_2: str,
                            setting: str, series_key: str = "", duration: int = 15) -> list[Script]:
        """Generate 3 script options (savage, wholesome, absurd) for user to pick."""
        options = []
        for tone in ["savage", "wholesome", "absurd"]:
            script = self.write(topic, character_1, character_2, setting, series_key, duration, tone)
            options.append(script)
        return options

    def format_for_telegram(self, options: list[Script]) -> str:
        """Format 3 script options for Telegram."""
        msg = "📝 Pick a script (reply 1, 2, or 3):\n\n"
        for i, script in enumerate(options):
            msg += f"{i + 1}. [{script.lines[0].get('speaker', '').upper() if script.lines else 'UNKNOWN'}]\n"
            for line in script.lines[:4]:  # Show first 4 lines as preview
                msg += f"  {line['speaker'].replace('_', ' ').title()}: \"{line['line']}\"\n"
            if len(script.lines) > 4:
                msg += f"  ... ({len(script.lines) - 4} more lines)\n"
            msg += "\n"
        return msg
