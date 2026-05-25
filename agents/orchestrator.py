"""Orchestrator — simple pipeline for What-If short-form videos."""

import logging
import re
import subprocess
from datetime import date
from pathlib import Path

import yaml

from agents.kling_video import KlingVideoGenerator
from agents.thought_generator import ThoughtGenerator
from agents.uploader import YouTubeUploader
from agents.voice_generator import VoiceGenerator
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


class Orchestrator:
    """Pipeline: thought → voice → kling video → merge → upload."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path, duration: float) -> None:
        """Merge Kling video with voiceover, trimming to audio length."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            # Scale to 1080x1920 if not already
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, "merge_video_audio")

    def run_daily(self, run_date: str | None = None, dry_run: bool = False, count: int = 1) -> dict:
        """Generate and upload what-if videos.

        Args:
            run_date: Date string.
            dry_run: Skip upload if True.
            count: Number of videos to produce.
        """
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {"date": run_date, "status": "started", "videos": []}

        logger.info(f"{'=' * 50}")
        logger.info(f"WHAT IF — Daily Pipeline | {run_date}")
        logger.info(f"Videos to produce: {count} | Dry run: {dry_run}")
        logger.info(f"{'=' * 50}")

        try:
            thought_gen = ThoughtGenerator()
            voice_gen = VoiceGenerator()
            kling = KlingVideoGenerator()
            uploader = YouTubeUploader()

            for i in range(count):
                logger.info(f"\n--- Video {i + 1}/{count} ---")

                # Step 1: Generate thought (check Telegram → file → auto-generate)
                logger.info("[1/4] Generating thought...")
                telegram = TelegramBot()

                # Check Telegram for a seed from user
                user_seed = telegram.get_latest_message(max_age_hours=24) or ""

                # Fallback: check seed file
                if not user_seed:
                    seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"
                    if seed_file.exists():
                        user_seed = seed_file.read_text().strip()
                        if user_seed:
                            seed_file.write_text("")

                # Check if user wants a longer video
                make_long = False
                if user_seed:
                    if "long" in user_seed.lower():
                        make_long = True
                        user_seed = user_seed.lower().replace("long", "").strip(" -,.")
                    logger.info(f"  Using seed: {user_seed[:60]}... ({'long' if make_long else 'short'})")
                    thought = thought_gen.run(category_hint=user_seed, long_form=make_long)
                else:
                    thought = thought_gen.run()

                # Step 2: Generate voiceover
                logger.info("[2/4] Generating voiceover...")
                voice_text = thought.text
                # Clean for TTS
                clean_text = re.sub(r'\[.*?\]', '', voice_text).strip()

                audio_path = output_dir / f"voice_{i:02d}.wav"
                raw_path = output_dir / f"voice_{i:02d}_raw.mp3"

                voice_gen._generate_audio(clean_text, raw_path)

                from utils.audio_processing import process_narration
                speed = self.config["voice"].get("speed_multiplier", 1.0)
                ambient_path = voice_gen._pick_ambient_track()
                process_narration(
                    raw_audio_path=raw_path,
                    output_path=audio_path,
                    ambient_path=ambient_path,
                    target_lufs=self.config["audio"]["target_lufs"],
                    bass_boost_db=self.config["audio"]["bass_boost_db"],
                    bass_freq=self.config["audio"]["bass_freq_hz"],
                    high_cut_freq=self.config["audio"]["high_cut_freq_hz"],
                    compression_threshold=self.config["audio"]["compression_threshold_db"],
                    compression_ratio=self.config["audio"]["compression_ratio"],
                    ambient_volume_db=self.config["audio"]["ambient_volume_db"],
                    speed_multiplier=speed,
                )

                # Get audio duration
                from pydub import AudioSegment
                audio = AudioSegment.from_file(audio_path)
                audio_duration = len(audio) / 1000.0
                logger.info(f"  Voice: {audio_duration:.1f}s")

                # Step 3: Generate Kling video clips (one per visual scene)
                logger.info(f"[3/4] Generating {len(thought.visual_scenes)} Kling clips...")
                kling_path = output_dir / f"kling_{i:02d}.mp4"

                # Calculate duration per clip to cover audio
                num_scenes = len(thought.visual_scenes) or 1
                secs_per_clip = min(10, max(5, int(audio_duration / num_scenes) + 1))

                clip_paths = []
                for s_idx, scene_prompt in enumerate(thought.visual_scenes):
                    clip_path = output_dir / f"kling_{i:02d}_scene_{s_idx}.mp4"
                    logger.info(f"  Scene {s_idx + 1}/{num_scenes}: {scene_prompt[:60]}...")
                    kling.generate(scene_prompt, clip_path, duration=secs_per_clip)
                    clip_paths.append(clip_path)

                # Stitch all clips together
                if len(clip_paths) == 1:
                    clip_paths[0].rename(kling_path)
                else:
                    concat_file = output_dir / f"concat_{i:02d}.txt"
                    concat_file.write_text("".join(f"file '{p}'\n" for p in clip_paths))
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(concat_file), "-c", "copy", str(kling_path)
                    ], capture_output=True)
                    concat_file.unlink()
                    for p in clip_paths:
                        p.unlink(missing_ok=True)

                # Step 4: Merge video + audio
                logger.info("[4/4] Merging video + audio...")
                final_path = output_dir / f"final_{i:02d}.mp4"
                self._merge_video_audio(kling_path, audio_path, final_path, audio_duration)

                # Send to Telegram for approval
                title = thought.hook_text
                description = f"{thought.text}\n\n#shorts #whatif #scifi #mindblown #paralleluniverse"
                tags = ["what if", "mind bending", "sci-fi", "time travel", "parallel universe",
                        "alternate history", "simulation theory", "shorts"]

                if dry_run:
                    logger.info(f"  [DRY RUN] Would upload: '{title}'")
                else:
                    # Send video to Telegram and wait for approval
                    logger.info("  Sending video for approval on Telegram...")
                    telegram.send_video_for_approval(str(final_path), thought.text, audio_duration)

                    approval = telegram.wait_for_approval(timeout_minutes=30)

                    if approval:
                        logger.info("  Approved! Uploading...")
                        uploader.run(
                            long_form_path=final_path,
                            short_paths=[],
                            title=title,
                            description=description,
                            tags=tags,
                            thumbnail_path=final_path,
                            dry_run=False,
                        )
                    else:
                        logger.info("  Rejected or timed out. Skipping upload.")

                summary["videos"].append({
                    "thought": thought.text[:80],
                    "category": thought.category,
                    "duration": audio_duration,
                    "title": title,
                    "path": str(final_path),
                })

            summary["status"] = "success"

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            summary["status"] = "failed"
            summary["error"] = str(e)

        logger.info(f"\n{'=' * 50}")
        logger.info(f"Pipeline: {summary['status']} | {len(summary['videos'])} videos")
        logger.info(f"{'=' * 50}")

        return summary
