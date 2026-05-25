"""Voice Generator Agent — ElevenLabs TTS with audio post-processing (per-part)."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from elevenlabs import ElevenLabs

from agents.script_writer import Script
from utils.audio_processing import process_narration
from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
VOICES_PATH = Path(__file__).parent.parent / "config" / "voices.yaml"


@dataclass
class VoiceOutput:
    raw_audio_path: Path
    processed_audio_path: Path
    duration_seconds: float
    characters_used: int


class VoiceGenerator:
    """Generates narration audio from scripts using ElevenLabs TTS."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = ElevenLabs()
        self.voice_config = self.config["voice"]
        self.audio_config = self.config["audio"]

    def _clean_for_tts(self, text: str) -> str:
        """Remove markers and prepare text for TTS."""
        text = re.sub(r'\[SFX:.*?\]', '', text)
        text = re.sub(r'\[VIDEO:.*?\]', '', text)
        text = re.sub(r'\[PAUSE \d+\.?\d*s\]', '...', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace('... ...', '...')
        return text

    @retry(max_attempts=3, base_delay=5.0)
    def _generate_audio(self, text: str, output_path: Path) -> int:
        """Call ElevenLabs API to generate audio. Returns character count."""
        audio_generator = self.client.text_to_speech.convert(
            voice_id=self.voice_config["voice_id"],
            model_id=self.voice_config["model_id"],
            text=text,
            voice_settings={
                "stability": self.voice_config["stability"],
                "similarity_boost": self.voice_config["similarity_boost"],
                "style": self.voice_config["style"],
            },
        )

        with open(output_path, "wb") as f:
            for chunk in audio_generator:
                f.write(chunk)

        char_count = len(text)
        cost = char_count * 0.000167
        log_cost("elevenlabs", char_count, cost)
        return char_count

    def _pick_ambient_track(self) -> Path | None:
        """Pick a random ambient drone track from assets."""
        import random
        drones_dir = Path(__file__).parent.parent / "assets" / "music" / "drones"
        if not drones_dir.exists():
            return None
        tracks = list(drones_dir.glob("*.mp3")) + list(drones_dir.glob("*.wav"))
        return random.choice(tracks) if tracks else None

    def generate_part(self, script: Script, part_index: int, output_dir: Path) -> VoiceOutput:
        """Generate narration audio for a single part.

        Args:
            script: Full multi-part script.
            part_index: Which part to generate (0-indexed).
            output_dir: Directory to save audio files.

        Returns:
            VoiceOutput for this part.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        part_num = part_index + 1
        raw_path = output_dir / f"narration_part_{part_num:02d}_raw.mp3"
        processed_path = output_dir / f"narration_part_{part_num:02d}.wav"

        # Get clean narration text for this part
        narration_text = script.get_narration_for_part(part_index)
        logger.info(f"Part {part_num}: generating voice for {len(narration_text)} chars")

        # Generate audio
        chars_used = self._generate_audio(narration_text, raw_path)

        # Post-process
        ambient_path = self._pick_ambient_track()
        speed = self.voice_config.get("speed_multiplier", 1.0)
        process_narration(
            raw_audio_path=raw_path,
            output_path=processed_path,
            ambient_path=ambient_path,
            target_lufs=self.audio_config["target_lufs"],
            bass_boost_db=self.audio_config["bass_boost_db"],
            bass_freq=self.audio_config["bass_freq_hz"],
            high_cut_freq=self.audio_config["high_cut_freq_hz"],
            compression_threshold=self.audio_config["compression_threshold_db"],
            compression_ratio=self.audio_config["compression_ratio"],
            ambient_volume_db=self.audio_config["ambient_volume_db"],
            speed_multiplier=speed,
        )

        # Get duration
        from pydub import AudioSegment
        audio = AudioSegment.from_file(processed_path)
        duration = len(audio) / 1000.0

        logger.info(f"Part {part_num}: {duration:.1f}s, {chars_used} chars")

        return VoiceOutput(
            raw_audio_path=raw_path,
            processed_audio_path=processed_path,
            duration_seconds=duration,
            characters_used=chars_used,
        )

    def run(self, script: Script, output_dir: Path) -> list[VoiceOutput]:
        """Generate narration audio for all parts.

        Args:
            script: Multi-part script.
            output_dir: Directory to save audio files.

        Returns:
            List of VoiceOutput, one per part.
        """
        outputs = []
        total_chars = 0

        for i in range(script.num_parts):
            voice_output = self.generate_part(script, i, output_dir)
            outputs.append(voice_output)
            total_chars += voice_output.characters_used

        logger.info(f"All parts generated: {len(outputs)} parts, {total_chars} total chars")
        return outputs
