"""Seedance 2.0 Video Generator — generates video with native TTS + lip sync via fal.ai."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import fal_client
import requests

from utils.cost_tracker import log_cost

logger = logging.getLogger(__name__)


@dataclass
class SeedanceClip:
    video_path: Path
    duration_seconds: float


class SeedanceVideoGenerator:
    """Generates video with built-in voice + lip sync using Seedance 2.0 on fal.ai."""

    def __init__(self):
        self.api_key = os.environ.get("FAL_KEY", "")
        os.environ["FAL_KEY"] = self.api_key
        self.model = "fal-ai/seedance-2/text-to-video"

    def generate(self, prompt: str, output_path: Path, duration: int = 5) -> SeedanceClip:
        """Generate a video clip with native TTS and lip sync.

        The prompt should include the dialogue in quotes — Seedance will generate
        the character speaking with lip sync and voice automatically.

        Args:
            prompt: Full scene description including dialogue in quotes.
            output_path: Where to save the video.
            duration: Clip duration (5-15 seconds).

        Returns:
            SeedanceClip with path and duration.
        """
        logger.info(f"Generating Seedance clip ({duration}s): {prompt[:80]}...")

        result = fal_client.subscribe(
            self.model,
            arguments={
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": "9:16",
            },
        )

        video_url = result["video"]["url"]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Download
        response = requests.get(video_url, timeout=120)
        response.raise_for_status()
        output_path.write_bytes(response.content)

        # Log cost (~$0.022/sec for fast, ~$0.247/sec for pro)
        cost = duration * 0.022
        log_cost("seedance", duration, cost)

        logger.info(f"  Done: {output_path} ({output_path.stat().st_size // 1024}KB)")
        return SeedanceClip(video_path=output_path, duration_seconds=duration)
