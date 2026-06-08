"""Trend Scanner — scans trending topics hourly and pre-generates pet comedy seeds.

Runs as a separate GitHub Action every hour. Saves trending seeds to
data/trending_seeds.json for the main pipeline to pull from.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import anthropic

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

TRENDING_SEEDS_PATH = Path(__file__).parent.parent / "data" / "trending_seeds.json"

SYSTEM_PROMPT = """You are a trend-spotter for a pet comedy video channel. Your job is to find trending topics that can be turned into funny pet drama scripts.

Scan your knowledge of current events, social media trends, and pop culture for topics that:
1. Are being discussed RIGHT NOW (this week)
2. Can be turned into a pet misunderstanding (cat/dog reacting to it)
3. Are relatable to a wide audience (not niche)
4. Have emotional energy (outrage, shock, confusion, humor)

For each trend, generate a pet comedy seed:
- Title: 2-3 words ALL CAPS
- Hook: The first line the pet would say
- Premise: How the pet misunderstands the situation
- Setting: An ABSURD location (not just "couch" — think courtroom, newsroom, stock trading floor)

OUTPUT JSON:
{
  "trends": [
    {
      "title": "AI TOOK JOBS",
      "hook": "A ROBOT is doing Dave's job now?",
      "premise": "Cat finds out AI replaced the human at work, doesn't understand why the robot can't give treats",
      "setting": "corporate office with robots at desks",
      "topic": "AI replacing jobs",
      "relevance_score": 9
    }
  ],
  "scanned_at": "2026-06-08T14:00:00"
}

Generate 10 trending seeds ranked by relevance_score (how likely people are talking about this RIGHT NOW)."""


class TrendScanner:
    """Scans trends hourly and saves pet comedy seeds."""

    def __init__(self):
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=2, base_delay=5.0)
    def scan(self) -> list[dict]:
        """Scan for trending topics and generate pet comedy seeds."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Scan trends for {datetime.now().strftime('%B %d, %Y')}. What are people talking about this week that a pet would have a hilarious take on? Generate 10 seeds. JSON only."}],
        )
        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_trends", response.usage.input_tokens + response.usage.output_tokens, cost)

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            repaired = text
            if repaired.count('"') % 2 != 0:
                repaired += '"'
            open_b = repaired.count("[") - repaired.count("]")
            open_c = repaired.count("{") - repaired.count("}")
            repaired += "]" * open_b + "}" * open_c
            data = json.loads(repaired)

        trends = data.get("trends", [])
        trends.sort(key=lambda t: t.get("relevance_score", 0), reverse=True)

        logger.info(f"Scanned {len(trends)} trends. Top: {trends[0]['title'] if trends else 'none'}")
        return trends

    def save_trends(self, trends: list[dict]) -> None:
        """Save trending seeds to file for pipeline to use."""
        existing = []
        if TRENDING_SEEDS_PATH.exists():
            existing = json.loads(TRENDING_SEEDS_PATH.read_text()).get("trends", [])

        # Merge — keep last 24 hours of trends (deduplicate by title)
        seen = set()
        merged = []
        for t in trends + existing:
            if t["title"] not in seen:
                seen.add(t["title"])
                merged.append(t)

        # Keep top 20
        merged = merged[:20]

        TRENDING_SEEDS_PATH.write_text(json.dumps({
            "trends": merged,
            "last_scan": datetime.now().isoformat(),
        }, indent=2))

        logger.info(f"Saved {len(merged)} trending seeds to {TRENDING_SEEDS_PATH}")

    def run(self) -> None:
        """Full scan + save."""
        trends = self.scan()
        self.save_trends(trends)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env")

    scanner = TrendScanner()
    scanner.run()
