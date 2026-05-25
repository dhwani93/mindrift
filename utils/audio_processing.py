"""Audio post-processing utilities using pydub and ffmpeg."""

import logging
import subprocess
from pathlib import Path

from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, normalize

logger = logging.getLogger(__name__)


def get_loudness_lufs(audio_path: Path) -> float:
    """Measure loudness in LUFS using ffmpeg loudnorm filter."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(audio_path),
            "-af", "loudnorm=print_format=json",
            "-f", "null", "-"
        ],
        capture_output=True, text=True,
    )
    # Parse LUFS from ffmpeg stderr output
    import json as _json
    import re
    # Find the JSON block in stderr
    match = re.search(r'\{[^}]+\}', result.stderr, re.DOTALL)
    if match:
        data = _json.loads(match.group())
        return float(data.get("input_i", -16))
    return -16.0  # fallback


def normalize_loudness(audio: AudioSegment, target_lufs: float = -16) -> AudioSegment:
    """Normalize audio to target LUFS (approximate using peak normalization)."""
    # pydub doesn't do true LUFS, so we normalize to peak and adjust
    # For more precise LUFS, we rely on ffmpeg in the final mix step
    normalized = normalize(audio)
    # Adjust to approximate target (rough heuristic)
    current_dbfs = normalized.dBFS
    target_dbfs = target_lufs + 3  # LUFS is typically ~3dB below peak
    adjustment = target_dbfs - current_dbfs
    return normalized + adjustment


def apply_eq(audio: AudioSegment, bass_boost_db: float = 2.0, bass_freq: int = 120, high_cut_freq: int = 10000) -> AudioSegment:
    """Apply basic EQ: bass boost and high-frequency rolloff.

    For proper parametric EQ, we export to file and use ffmpeg's equalizer filter.
    This is a simplified version using pydub's low_pass/high_pass.
    """
    # Gentle high-frequency rolloff for warmth
    audio = audio.low_pass_filter(high_cut_freq)
    # Bass boost via duplicate overlay (simple approach)
    bass = audio.low_pass_filter(bass_freq)
    bass = bass + bass_boost_db
    # Mix the boosted bass back
    combined = audio.overlay(bass)
    return combined


def apply_compression(audio: AudioSegment, threshold: float = -20.0, ratio: float = 2.0) -> AudioSegment:
    """Apply dynamic range compression."""
    return compress_dynamic_range(audio, threshold=threshold, ratio=ratio)


def mix_ambient(narration: AudioSegment, ambient_path: Path, ambient_volume_db: float = -30) -> AudioSegment:
    """Mix a quiet ambient pad underneath the narration.

    The ambient track is looped to match narration length and mixed at the specified volume.
    """
    ambient = AudioSegment.from_file(ambient_path)

    # Loop ambient to cover full narration
    loops_needed = (len(narration) // len(ambient)) + 1
    ambient_looped = ambient * loops_needed
    ambient_looped = ambient_looped[:len(narration)]

    # Set ambient volume relative to narration
    ambient_looped = ambient_looped + (ambient_volume_db - ambient_looped.dBFS)

    # Fade ambient in and out
    ambient_looped = ambient_looped.fade_in(3000).fade_out(3000)

    return narration.overlay(ambient_looped)


def speed_up_audio(audio_path: Path, output_path: Path, speed: float = 1.15) -> Path:
    """Speed up audio using FFmpeg atempo filter (preserves pitch)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-filter:a", f"atempo={speed}",
        "-vn",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"Speed-up failed, keeping original: {result.stderr[-100:]}")
        import shutil
        shutil.copy2(audio_path, output_path)
    return output_path


def process_narration(
    raw_audio_path: Path,
    output_path: Path,
    ambient_path: Path | None = None,
    target_lufs: float = -16,
    bass_boost_db: float = 2.0,
    bass_freq: int = 120,
    high_cut_freq: int = 10000,
    compression_threshold: float = -20.0,
    compression_ratio: float = 2.0,
    ambient_volume_db: float = -30,
    speed_multiplier: float = 1.0,
) -> Path:
    """Full narration post-processing pipeline.

    Steps:
    1. Normalize loudness
    2. Apply EQ (bass boost + high cut)
    3. Apply compression
    4. Mix ambient pad (if provided)
    5. Export as WAV

    Returns:
        Path to the processed audio file.
    """
    logger.info(f"Processing narration: {raw_audio_path}")

    audio = AudioSegment.from_file(raw_audio_path)

    # Step 1: Normalize
    audio = normalize_loudness(audio, target_lufs)

    # Step 2: EQ
    audio = apply_eq(audio, bass_boost_db, bass_freq, high_cut_freq)

    # Step 3: Compression
    audio = apply_compression(audio, compression_threshold, compression_ratio)

    # Step 4: Mix ambient
    if ambient_path and ambient_path.exists():
        audio = mix_ambient(audio, ambient_path, ambient_volume_db)
        logger.info(f"Mixed ambient track: {ambient_path.name}")

    # Step 5: Export (temp if we need to speed up)
    if speed_multiplier > 1.0:
        temp_path = output_path.parent / f"{output_path.stem}_prespeed.wav"
        audio.export(str(temp_path), format="wav")
        speed_up_audio(temp_path, output_path, speed=speed_multiplier)
        temp_path.unlink(missing_ok=True)
        logger.info(f"Processed + sped up ({speed_multiplier}x): {output_path}")
    else:
        audio.export(str(output_path), format="wav")

    # Get final duration
    final = AudioSegment.from_file(output_path)
    logger.info(f"Processed audio saved: {output_path} ({len(final) / 1000:.1f}s)")

    return output_path
