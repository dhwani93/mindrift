"""Subtitle generator — creates SRT files from narration text with timing."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt_from_text(
    narration_text: str,
    total_duration_seconds: float,
    output_path: Path,
    words_per_subtitle: int = 8,
) -> Path:
    """Generate an SRT subtitle file by evenly distributing text across the duration.

    This is a simple approach that splits text into chunks and distributes them
    evenly across the audio duration. For more precise timing, use word-level
    timestamps from ElevenLabs or Whisper.

    Args:
        narration_text: Full narration text (with markers stripped).
        total_duration_seconds: Total audio duration in seconds.
        output_path: Where to save the .srt file.
        words_per_subtitle: Number of words per subtitle chunk.

    Returns:
        Path to the generated SRT file.
    """
    # Clean text of any remaining markers
    clean_text = re.sub(r'\[.*?\]', '', narration_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    # Split into words
    words = clean_text.split()
    total_words = len(words)

    if total_words == 0:
        logger.warning("No words to generate subtitles from")
        output_path.write_text("")
        return output_path

    # Create subtitle chunks
    chunks = []
    for i in range(0, total_words, words_per_subtitle):
        chunk_words = words[i:i + words_per_subtitle]
        chunks.append(" ".join(chunk_words))

    # Calculate timing for each chunk
    time_per_chunk = total_duration_seconds / len(chunks)

    # Build SRT content
    srt_lines = []
    for i, chunk in enumerate(chunks):
        start_time = i * time_per_chunk
        end_time = (i + 1) * time_per_chunk
        srt_lines.append(f"{i + 1}")
        srt_lines.append(f"{_format_srt_time(start_time)} --> {_format_srt_time(end_time)}")
        srt_lines.append(chunk)
        srt_lines.append("")

    output_path.write_text("\n".join(srt_lines), encoding="utf-8")
    logger.info(f"Generated {len(chunks)} subtitle entries in {output_path}")
    return output_path
