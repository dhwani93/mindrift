"""Comparison test: ElevenLabs voiceover vs Kling lip sync.

Generates ONE realistic cat clip, then creates two versions:
A) ElevenLabs voice overlaid on silent video
B) Kling lip sync with TTS on the same video

Sends both to Telegram for user to compare.

Usage: python scripts/comparison_test.py
Cost: ~$0.77 total
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "config" / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from agents.kling_video import KlingVideoGenerator
from agents.kling_lipsync import KlingLipSync
from agents.voice_generator import VoiceGenerator
from utils.audio_processing import process_narration
from utils.telegram_bot import TelegramBot


def main():
    output_dir = Path(__file__).parent.parent / "output" / "comparison_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    kling = KlingVideoGenerator()
    lipsync = KlingLipSync()
    voice_gen = VoiceGenerator()
    telegram = TelegramBot()

    # The test script
    script = "They take HOW much? Every single year? And you just let them?"

    # --- STEP 1: Generate ONE realistic cat clip ---
    logger.info("=== Generating realistic cat clip (5s) ===")
    realistic_prompt = (
        "Static medium shot. Real orange tabby cat sitting on a beige couch in a cozy apartment, "
        "looking directly at camera with shocked wide eyes, ears perked forward, mouth slightly open. "
        "Warm afternoon sunlight through window, bookshelf behind. Cat's face clearly visible, "
        "expressive, photorealistic, ultra-realistic, cinematic lighting, shallow depth of field, 4K."
    )

    clip_path = output_dir / "base_clip.mp4"
    logger.info(f"  Prompt ({len(realistic_prompt.split())} words): {realistic_prompt[:80]}...")

    # Submit and get task_id (we need it for lip sync)
    task_id = kling._submit_generation(realistic_prompt, duration=5)
    video_url = kling._poll_task(task_id)
    kling._download_video(video_url, clip_path)
    logger.info(f"  Base clip generated: {clip_path}")

    # --- STEP 2: Test A — ElevenLabs voice overlaid ---
    logger.info("\n=== Test A: ElevenLabs voiceover ===")
    raw_audio = output_dir / "voice_raw.mp3"
    processed_audio = output_dir / "voice.wav"

    voice_gen._generate_audio(script, raw_audio)
    process_narration(
        raw_audio_path=raw_audio,
        output_path=processed_audio,
        ambient_path=voice_gen._pick_ambient_track(),
        target_lufs=-16,
        bass_boost_db=2,
        bass_freq=120,
        high_cut_freq=10000,
        compression_threshold=-20.0,
        compression_ratio=2.0,
        ambient_volume_db=-14,
    )

    # Merge video + audio
    test_a_path = output_dir / "test_A_elevenlabs.mp4"
    from pydub import AudioSegment
    audio_duration = len(AudioSegment.from_file(processed_audio)) / 1000.0

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-i", str(processed_audio),
        "-t", str(min(audio_duration, 5)),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        str(test_a_path),
    ], capture_output=True)
    logger.info(f"  Test A done: {test_a_path}")

    # --- STEP 3: Test B — Kling lip sync with TTS ---
    logger.info("\n=== Test B: Kling lip sync ===")
    test_b_path = output_dir / "test_B_lipsync.mp4"

    try:
        result = lipsync.run(
            video_task_id=task_id,
            text=script,
            output_path=test_b_path,
            voice="en_male_1",
        )
        logger.info(f"  Test B done: {test_b_path}")
    except Exception as e:
        logger.error(f"  Test B FAILED: {e}")
        test_b_path = None

    # --- STEP 4: Send both to Telegram ---
    logger.info("\n=== Sending to Telegram ===")
    telegram.send_message("🧪 COMPARISON TEST: Which looks better?\n\nTest A = ElevenLabs voiceover (no lip sync)\nTest B = Kling TTS with lip sync\n\nSending both now...")

    time.sleep(1)
    telegram.send_message("📹 TEST A — ElevenLabs voice overlay:")
    with open(test_a_path, "rb") as f:
        import requests as req
        req.post(
            f"https://api.telegram.org/bot{telegram.token}/sendVideo",
            data={"chat_id": telegram.chat_id, "caption": "Test A: ElevenLabs voiceover on silent video"},
            files={"video": f},
            timeout=60,
        )

    if test_b_path and test_b_path.exists():
        time.sleep(2)
        telegram.send_message("📹 TEST B — Kling lip sync:")
        with open(test_b_path, "rb") as f:
            import requests as req
            req.post(
                f"https://api.telegram.org/bot{telegram.token}/sendVideo",
                data={"chat_id": telegram.chat_id, "caption": "Test B: Kling TTS + lip sync"},
                files={"video": f},
                timeout=60,
            )
        telegram.send_message("Which is better? Reply A or B")
    else:
        telegram.send_message("❌ Test B (lip sync) failed. Only Test A available. Check if Kling lip sync works with AI-generated videos.")

    logger.info("\n=== Done! Check Telegram. ===")


if __name__ == "__main__":
    main()
