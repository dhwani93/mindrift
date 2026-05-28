"""Voice Generator — ElevenLabs TTS with audio post-processing."""

import logging
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from elevenlabs import ElevenLabs

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


class VoiceGenerator:
    """Generates voiceover audio using ElevenLabs TTS."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = ElevenLabs()
        self.voice_config = self.config["voice"]

    def _clean_for_tts(self, text: str) -> str:
        """Remove markers and prepare text for TTS."""
        text = re.sub(r'\[SFX:.*?\]', '', text)
        text = re.sub(r'\[VIDEO:.*?\]', '', text)
        text = re.sub(r'\[PAUSE \d+\.?\d*s\]', '...', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace('... ...', '...')
        # Add trailing pause for natural wind-down
        if not text.endswith('...'):
            text = text.rstrip('.!?') + '...'
        return text

    @retry(max_attempts=3, base_delay=5.0)
    def _generate_audio(self, text: str, output_path: Path) -> int:
        """Call ElevenLabs API to generate audio. Returns character count."""
        clean_text = self._clean_for_tts(text)

        audio_generator = self.client.text_to_speech.convert(
            voice_id=self.voice_config["voice_id"],
            model_id=self.voice_config["model_id"],
            text=clean_text,
            voice_settings={
                "stability": self.voice_config["stability"],
                "similarity_boost": self.voice_config["similarity_boost"],
                "style": self.voice_config["style"],
            },
        )

        with open(output_path, "wb") as f:
            for chunk in audio_generator:
                f.write(chunk)

        char_count = len(clean_text)
        cost = char_count * 0.000167
        log_cost("elevenlabs", char_count, cost)
        return char_count

    def _pick_ambient_track(self) -> Path | None:
        """Pick a random ambient drone track from assets."""
        drones_dir = Path(__file__).parent.parent / "assets" / "music" / "drones"
        if not drones_dir.exists():
            return None
        tracks = list(drones_dir.glob("*.mp3")) + list(drones_dir.glob("*.wav"))
        return random.choice(tracks) if tracks else None
