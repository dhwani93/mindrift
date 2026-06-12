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
LIFE_TIMELINE_PATH = Path(__file__).parent.parent / "data" / "life_timeline.json"


def load_life_context() -> str:
    """Load compressed life context for the scriptwriter."""
    if LIFE_TIMELINE_PATH.exists():
        timeline = json.loads(LIFE_TIMELINE_PATH.read_text())
        ctx = timeline.get("compressed_context", "")
        era = timeline.get("current_era", "dating")
        status = timeline.get("relationship_status", "dating")
        work = timeline.get("work_status", "employee")
        return f"LUNA'S CURRENT LIFE: {ctx}\nEra: {era} | Relationship: {status} | Work: {work}"
    return "LUNA'S CURRENT LIFE: Luna is dating Milo. Works under Ms. Whiskers. Lives with Pickles."


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
        "catchphrases": ["LUNA SAID—", "AWK-WARD!", "LUNA HATES HER—"],
        "never_says": "I'll keep that to myself.",
    },
    "jade": {
        "name": "Jade",
        "personality": "Luna's human best friend and coworker. The only person who gives REAL advice. Down-to-earth, no drama, straight talk. Says 'girl, same' a lot.",
        "speech_style": "Real talk. Supportive but blunt. 'Girl, same.' Uses 'I' when sharing her own experience, 'you' when giving advice. No sugarcoating.",
        "catchphrases": ["Girl, same.", "Been there. Did NOT get the t-shirt.", "You deserve better. But also, same.", "That's not a red flag, that's a red BANNER.", "I say this with love — what the hell."],
        "never_says": "That sounds fine. Don't worry about it. I'm sure it'll work out.",
    },
    # === SEASON 2 ===
    "tiffany": {
        "name": "Tiffany",
        "personality": "Luna's rich friend. Overdressed for EVERYTHING. New boyfriend every week. Drama queen. 'Darling' energy.",
        "speech_style": "Dramatic, self-centered. 'Darling, you won't BELIEVE.' Name-drops constantly. Uses 'I' to make everything about herself.",
        "catchphrases": ["Darling, you won't BELIEVE.", "He was perfect. Until he wasn't.", "I'm not overdressed. You're all underdressed.", "This calls for champagne."],
        "never_says": "I'll wear something casual. I'm fine being single.",
    },
    "boba": {
        "name": "Boba",
        "personality": "Luna's younger sister (kitten). Broke but acts expensive. Crashes on Luna's couch. Zero responsibility. Gen Z energy.",
        "speech_style": "Fast, unbothered, Gen Z slang. 'It's called self-care.' Uses 'I' with complete confidence about terrible decisions.",
        "catchphrases": ["It's called self-care.", "I'll pay you back. Eventually.", "Why are you stressing? Just manifest it.", "What's yours is mine. We're family."],
        "never_says": "I'll get a job. I'll move out. That's too expensive for me.",
    },
    "dave": {
        "name": "Dave",
        "personality": "Milo's best friend. Senior dog. Gives unsolicited advice that's technically correct but unhelpful. HR manager energy. Exhausted.",
        "speech_style": "Dry, understated. Sighs before speaking. 'In MY day...' Uses 'I' with the authority of someone who's seen too much.",
        "catchphrases": ["In MY day, we handled things differently.", "Have you considered... not doing that?", "I didn't ask, but here's my opinion.", "I need a nap after this conversation."],
        "never_says": "That sounds fun! Great idea! Let's be spontaneous!",
    },
    # === SEASON 3 ===
    "priya": {
        "name": "Priya",
        "personality": "Luna's friend. ALWAYS has in-law problems. Every conversation leads back to her mother-in-law. Boundary issues personified.",
        "speech_style": "Starts normal, spirals into MIL rant. 'You won't believe what she said THIS time.' Uses 'she' for MIL with venom.",
        "catchphrases": ["You won't believe what she said THIS time.", "She showed up. Unannounced. AGAIN.", "It's not about the food. It's about RESPECT.", "I'm one comment away from moving countries."],
        "never_says": "My mother-in-law is wonderful. She means well.",
    },
    "marco": {
        "name": "Marco",
        "personality": "Luna's friend. ALWAYS has money problems but pitches get-rich-quick schemes. Crypto, NFTs, pyramid schemes. Confidently wrong.",
        "speech_style": "Sales pitch energy. 'Bro, hear me out.' Uses 'we' to drag others into his schemes. Never accepts fault.",
        "catchphrases": ["Bro, hear me out.", "This is NOT a pyramid scheme.", "I just need a small investment.", "Trust me, I did the research.", "It's called passive income."],
        "never_says": "Maybe I should save money. That sounds risky.",
    },
    "karen_mil": {
        "name": "Karen",
        "personality": "Milo's mom. THE mother-in-law. Passive-aggressive compliments. Thinks nobody is good enough for her son. Weaponized politeness.",
        "speech_style": "Sweet voice, devastating words. 'Oh Luna, you cooked? How... brave.' Uses 'dear' before every insult.",
        "catchphrases": ["Oh dear, you tried.", "How... interesting.", "My Milo deserves...", "I'm not saying anything, BUT...", "When I was your age, I had already..."],
        "never_says": "Luna is perfect for my son. I love what you've done with the place.",
    },
    "cleo": {
        "name": "Cleo",
        "personality": "Luna's toxic friend. Makes EVERYTHING about herself. Master of backhanded compliments. One-upper.",
        "speech_style": "Interrupts constantly. 'That's rough, but let me tell you what happened to ME.' Starts with fake empathy, pivots to self.",
        "catchphrases": ["That's rough. Anyway, so I—", "Not to make this about me, BUT—", "Oh you think THAT'S bad?", "I've been through worse, honestly."],
        "never_says": "Tell me more. How are YOU feeling? That must be hard for you.",
    },
    "gary": {
        "name": "Gary",
        "personality": "The complainer. NOTHING is ever good enough. Food is cold. Weather is wrong. Hates everything. 'This is fine' while visibly not fine.",
        "speech_style": "Monotone negativity. Deadpan. Every sentence is a complaint disguised as an observation.",
        "catchphrases": ["This is fine.", "Could be worse. Actually, no, this is the worst.", "I expected nothing and I'm still disappointed.", "Why do I even bother."],
        "never_says": "This is great! I'm having a wonderful time! What a beautiful day!",
    },
    "suki": {
        "name": "Suki",
        "personality": "The wellness guru. Essential oils solve everything. Judges everyone's food choices. 'Have you tried manifesting?' energy.",
        "speech_style": "Calm, condescending serenity. 'Your energy is really... something today.' Passive-aggressive about health choices.",
        "catchphrases": ["Have you tried manifesting?", "Your energy is really... something today.", "I don't eat that. Do you know what's IN that?", "I'll send you my healer's number."],
        "never_says": "Let's get fast food. Stress is normal. Whatever works for you.",
    },
    "rex": {
        "name": "Rex",
        "personality": "Milo's gym bro friend. Everything is a workout metaphor. Cannot read the room. Zero emotional intelligence. Means well, just... no.",
        "speech_style": "Bro energy. 'Relationships are like deadlifts, bro.' Gives terrible advice with complete confidence.",
        "catchphrases": ["Bro, relationships are like deadlifts.", "You just gotta push through it.", "That's a mental gains opportunity.", "No pain, no gain. Emotionally too."],
        "never_says": "Let's talk about feelings. I understand. Take your time.",
    },
}

SYSTEM_PROMPT = """You are a COMEDY SCRIPTWRITER for short-form pet drama videos (15-30 seconds).

You write DIALOGUE between two animal characters. NOT monologue. NOT narration. A real back-and-forth CONVERSATION.

ABSOLUTE RULES:
1. Label EVERY line with the CHARACTER NAME: "LUNA:", "MILO:", "MS. WHISKERS:", "PICKLES:" etc.
   NEVER use species names like "orange cat" or "golden retriever". Use their NAMES.
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

CHARACTER GENDERS (respect these ALWAYS):
- Luna = female (she/her)
- Milo = male (he/him)
- Ms. Whiskers = female (she/her)
- Pickles = gender neutral (it/they)
- Jade = female (she/her) — human woman
- Tiffany = female, Boba = female, Priya = female, Cleo = female, Suki = female
- Dave = male, Marco = male, Gary = male, Rex = male, Karen = female

GOOD DIALOGUE EXAMPLE (15s, office drama):
```
LUNA: You took credit for MY presentation?
MS. WHISKERS: I improved it.
LUNA: You changed ONE slide.
MS. WHISKERS: The important one.
LUNA: It was the TITLE slide.
MS. WHISKERS: And now it has MY name on it.
```
(6 lines. Back and forth. Each reacts to the other. Punchline at end.)

GOOD DIALOGUE EXAMPLE (15s, couple drama):
```
LUNA: You spent four hundred dollars.
MILO: It was an investment.
LUNA: In SHOES?
MILO: They were on sale!
LUNA: That's not how sales work!
MILO: I also bought you flowers. With your card.
```

GOOD DIALOGUE EXAMPLE (15s, Luna vents to Jade):
```
LUNA: My boss stole my presentation. Again.
JADE: Girl, same. My manager did that last Tuesday.
LUNA: What did you do?
JADE: Quiet quit. I just do the minimum now.
LUNA: That's... actually genius.
JADE: Welcome to corporate survival.
```

BAD DIALOGUE (uses species names instead of character names — NEVER do this):
```
"ORANGE CAT: You know what really bothers me..."
"GOLDEN RETRIEVER: What's that?"
```
(WRONG! Use LUNA, MILO, MS. WHISKERS, PICKLES, JADE — NEVER species names!)

BAD DIALOGUE:
```
LUNA: You did something bad.
LUNA: And it was really bad.
LUNA: I can't believe you did that.
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

CHARACTER INTRODUCTION RULE:
If a character is appearing for the FIRST TIME EVER (context will say "FIRST APPEARANCE"),
DON'T write a formal intro. Write a SCENE that naturally SHOWS who they are through ACTION.

SHOW, DON'T TELL. Never have Luna say "This is my boyfriend Milo." Instead SHOW Milo being Milo.

INTRO EPISODE FORMULA:
- Put the character in a SITUATION that reveals their personality
- Their first line IS their personality (not a greeting)
- Luna reacts to them in a way that shows their relationship
- The audience figures out who they are from the DYNAMIC, not exposition

EXAMPLE — Luna's intro (EP.1):
SCENE: Luna wakes up late, hair disaster, rushing, apartment is chaos.
LUNA: "NO. No no no. I'm LATE."
LUNA: "MILO! Why didn't you wake me?!"
MILO: (eating cereal calmly) "You said, and I quote, 'wake me up and I'll end you.'"
LUNA: "That was OBVIOUSLY a joke!"
PICKLES: "OBVIOUSLY A JO—"
LUNA: "PICKLES. NOT NOW."
→ Audience learns: Luna is dramatic/chaotic. Milo is clueless but quotes her. Pickles repeats everything.

EXAMPLE — Ms. Whiskers intro:
SCENE: Luna arrives at office 1 minute late.
MS. WHISKERS: "Luna. You're late."
LUNA: "By ONE minute."
MS. WHISKERS: "Noted. I'll document that. Also, I need you to redo the presentation. My name should be bigger."
→ Audience learns: Boss is a nightmare. Luna suffers daily.

After their intro episode, they're established and don't need re-introduction.

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
    {"speaker": "luna", "line": "The actual dialogue"},
    {"speaker": "milo", "line": "The response"}
  ],
  "visual_notes": "Brief description of setting and what characters are doing physically"
}
"""


@dataclass
class Script:
    title: str
    duration_sec: int
    lines: list[dict]  # [{"speaker": "luna", "line": "..."}]
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

        # Load Luna's life context
        life_context = load_life_context()

        # Check if characters are being introduced for the first time
        # Name-to-key mapping (duplicated here to avoid circular import)
        _name_to_key = {
            "luna": "luna", "orange_cat": "luna", "milo": "milo", "golden_retriever": "milo",
            "ms. whiskers": "ms_whiskers", "ms_whiskers": "ms_whiskers", "white_cat": "ms_whiskers",
            "pickles": "pickles", "jade": "jade", "tiffany": "tiffany", "boba": "boba",
            "dave": "dave", "priya": "priya", "marco": "marco", "karen": "karen_mil",
        }
        intro_context = ""
        if LIFE_TIMELINE_PATH.exists():
            timeline = json.loads(LIFE_TIMELINE_PATH.read_text())
            introduced = timeline.get("characters_introduced", [])
            new_chars = []
            # Check both name and key formats
            char1_introduced = character_1 in introduced or _name_to_key.get(character_1, character_1) in introduced
            char2_introduced = character_2 in introduced or _name_to_key.get(character_2, character_2) in introduced
            if not char1_introduced:
                new_chars.append(character_1)
            if not char2_introduced and character_2 != character_1:
                new_chars.append(character_2)
            if new_chars:
                intro_context = f"\nFIRST APPEARANCE: {', '.join(new_chars)} is/are appearing for the FIRST TIME. Their first line must establish who they are and their relationship to Luna. Make it natural, not forced."

        # Get today's day for day awareness
        from datetime import datetime
        day_name = datetime.now().strftime("%A")

        prompt = f"""Write a {duration}-second pet drama dialogue.

{life_context}
TODAY IS: {day_name}
{intro_context}

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
