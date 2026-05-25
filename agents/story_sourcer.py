"""Story Sourcer Agent — selects stories from curated DB or generates originals via Claude."""

import hashlib
import json
import logging
import random
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
SOURCES_PATH = Path(__file__).parent.parent / "data" / "story_sources.json"
DB_PATH = Path(__file__).parent.parent / "data" / "content.db"


@dataclass
class RawStory:
    title: str
    body: str
    source: str
    source_url: str | None
    category: str
    body_hash: str


class StorySourcer:
    """Finds and selects stories for Mindrift pipeline."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        with open(SOURCES_PATH) as f:
            self.sources = json.load(f)
        self.content_config = self.config["content"]

    def _get_today_source(self) -> str:
        """Determine which source to use based on the day of the week."""
        day_name = date.today().strftime("%A").lower()
        return self.content_config["weekly_schedule"].get(day_name, "original")

    def _is_duplicate(self, body_hash: str) -> bool:
        """Check if a story with this hash has already been used."""
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT id FROM stories WHERE body_hash = ?", (body_hash,)
        ).fetchone()
        conn.close()
        return row is not None

    def _save_story(self, story: RawStory) -> None:
        """Save story to the database."""
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO stories (title, body_hash, source, source_url, category, raw_text) VALUES (?, ?, ?, ?, ?, ?)",
            (story.title, story.body_hash, story.source, story.source_url, story.category, story.body),
        )
        conn.commit()
        conn.close()

    def _generate_original(self) -> RawStory:
        """Select a seed prompt for Claude to expand into an original story."""
        categories = list(self.sources["seed_prompts"].keys())
        category = random.choice(categories)
        prompts = self.sources["seed_prompts"][category]

        # Filter out previously used prompts
        available = []
        for prompt in prompts:
            body_hash = hashlib.sha256(prompt.encode()).hexdigest()
            if not self._is_duplicate(body_hash):
                available.append(prompt)

        if not available:
            # All prompts used, reset and pick random
            available = prompts
            logger.info("All seed prompts used, allowing repeats")

        prompt = random.choice(available)
        body_hash = hashlib.sha256(prompt.encode()).hexdigest()

        return RawStory(
            title="Original Story",
            body=prompt,
            source="original",
            source_url=None,
            category=category,
            body_hash=body_hash,
        )

    def _select_curated(self) -> RawStory | None:
        """Select a curated true crime / historical mystery entry."""
        entries = self.sources.get("true_crime_curated", [])
        random.shuffle(entries)

        for entry in entries:
            body_hash = hashlib.sha256(entry["summary"].encode()).hexdigest()
            if not self._is_duplicate(body_hash):
                return RawStory(
                    title=entry["title"],
                    body=entry["summary"],
                    source="curated",
                    source_url=None,
                    category=entry.get("category", "unsolved_mystery"),
                    body_hash=body_hash,
                )

        logger.warning("All curated stories used")
        return None

    def run(self) -> RawStory:
        """Select a story for today's video.

        Uses the weekly schedule to determine the source, with fallbacks.

        Returns:
            A RawStory ready for the ScriptWriter.
        """
        source_type = self._get_today_source()
        logger.info(f"Today's source type: {source_type}")

        story = None

        if source_type == "true_crime_curated":
            story = self._select_curated()
            if not story:
                logger.info("Curated fallback → original")
                story = self._generate_original()
        else:  # original
            story = self._generate_original()

        # Save to database
        self._save_story(story)
        logger.info(f"Selected story: '{story.title}' [{story.category}] from {story.source}")

        return story
