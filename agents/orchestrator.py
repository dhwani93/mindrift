"""Orchestrator — pipeline for Mindrift short-form videos."""

import logging
import re
import subprocess
from datetime import date
from pathlib import Path

import yaml

from agents.kling_prompt_builder import KlingPromptBuilder
from agents.kling_video import KlingVideoGenerator
from agents.thought_generator import ThoughtGenerator
from agents.uploader import YouTubeUploader
from agents.voice_generator import VoiceGenerator
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


class Orchestrator:
    """Pipeline: thought → voice → Kling video → merge → Telegram approval → upload."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _add_captions(self, input_path: Path, output_path: Path, text: str, duration: float) -> None:
        """Add word-by-word captions in TikTok/Reels style.

        Shows 3 words at a time, large white text with thick black outline,
        centered at 75% height.
        """
        clean = re.sub(r'\[.*?\]', '', text).strip()
        words = clean.split()

        chunk_size = 3
        chunks = []
        for j in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[j:j + chunk_size]))

        if not chunks:
            import shutil
            shutil.copy2(input_path, output_path)
            return

        time_per_chunk = duration / len(chunks)

        filters = []
        for idx, chunk in enumerate(chunks):
            start = idx * time_per_chunk
            end = (idx + 1) * time_per_chunk
            safe_chunk = chunk.replace("'", "\u2019").replace(":", "\\:").replace("\\", "")
            filters.append(
                f"drawtext=text='{safe_chunk}'"
                f":fontsize=52:fontcolor=white:borderw=4:bordercolor=black"
                f":x=(w-text_w)/2:y=h*0.75"
                f":enable='between(t,{start:.2f},{end:.2f})'"
            )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy", "-pix_fmt", "yuv420p",
            str(output_path),
        ]

        try:
            self._run_ffmpeg(cmd, "add_captions")
        except RuntimeError as e:
            logger.warning(f"Captions failed (drawtext missing): {e}")
            import shutil
            shutil.copy2(input_path, output_path)

    def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path, duration: float) -> None:
        """Merge Kling video with voiceover, trimming to exact audio length."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, "merge_video_audio")

    def run_daily(self, run_date: str | None = None, dry_run: bool = False, count: int = 1) -> dict:
        """Generate and upload a Mindrift video.

        Args:
            run_date: Date string (YYYY-MM-DD). Defaults to today.
            dry_run: If True, generate video but skip upload and approval.
            count: Number of videos to produce.
        """
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {"date": run_date, "status": "started", "videos": []}

        logger.info(f"{'=' * 50}")
        logger.info(f"MINDRIFT — Daily Pipeline | {run_date}")
        logger.info(f"Videos: {count} | Dry run: {dry_run}")
        logger.info(f"{'=' * 50}")

        try:
            thought_gen = ThoughtGenerator()
            voice_gen = VoiceGenerator()
            prompt_builder = KlingPromptBuilder()
            kling = KlingVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            for i in range(count):
                logger.info(f"\n--- Video {i + 1}/{count} ---")

                # === STEP 1: Get thought seed ===
                logger.info("[1/5] Getting thought seed...")

                # Check Telegram for user's seed (within last 3 hours = since reminder)
                user_seed = telegram.get_latest_message(max_age_hours=3) or ""

                # Fallback: check seed file
                if not user_seed:
                    seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"
                    if seed_file.exists():
                        user_seed = seed_file.read_text().strip()
                        if user_seed:
                            seed_file.write_text("")

                # Parse "long" modifier
                make_long = False
                if user_seed:
                    if "long" in user_seed.lower():
                        make_long = True
                        user_seed = user_seed.lower().replace("long", "").strip(" -,.")
                    logger.info(f"  Seed: {user_seed[:60]}... ({'long' if make_long else 'short'})")
                    thought = thought_gen.run(category_hint=user_seed, long_form=make_long)
                else:
                    logger.info("  No seed — auto-generating")
                    thought = thought_gen.run()

                # === STEP 2: Generate voiceover ===
                logger.info("[2/5] Generating voiceover...")
                clean_text = re.sub(r'\[.*?\]', '', thought.text).strip()

                audio_path = output_dir / f"voice_{i:02d}.wav"
                raw_path = output_dir / f"voice_{i:02d}_raw.mp3"

                voice_gen._generate_audio(clean_text, raw_path)

                from utils.audio_processing import process_narration
                process_narration(
                    raw_audio_path=raw_path,
                    output_path=audio_path,
                    ambient_path=voice_gen._pick_ambient_track(),
                    target_lufs=self.config["audio"]["target_lufs"],
                    bass_boost_db=self.config["audio"]["bass_boost_db"],
                    bass_freq=self.config["audio"]["bass_freq_hz"],
                    high_cut_freq=self.config["audio"]["high_cut_freq_hz"],
                    compression_threshold=self.config["audio"]["compression_threshold_db"],
                    compression_ratio=self.config["audio"]["compression_ratio"],
                    ambient_volume_db=self.config["audio"]["ambient_volume_db"],
                    speed_multiplier=self.config["voice"].get("speed_multiplier", 1.0),
                )

                from pydub import AudioSegment
                audio = AudioSegment.from_file(audio_path)
                audio_duration = len(audio) / 1000.0

                # Enforce 15-20 second range
                min_duration = 15
                max_duration = self.config["content"].get("max_video_duration_sec", 20)

                if audio_duration < min_duration:
                    logger.warning(f"  Audio {audio_duration:.1f}s is under {min_duration}s, padding with silence")
                    pad_ms = int((min_duration - audio_duration) * 1000)
                    silence = AudioSegment.silent(duration=pad_ms)
                    audio = audio + silence
                    audio.export(str(audio_path), format="wav")
                    audio_duration = min_duration

                if audio_duration > max_duration:
                    logger.warning(f"  Audio {audio_duration:.1f}s exceeds {max_duration}s cap, trimming")
                    audio = audio[:max_duration * 1000].fade_out(500)
                    audio.export(str(audio_path), format="wav")
                    audio_duration = max_duration

                logger.info(f"  Voice: {audio_duration:.1f}s")

                # === STEP 3: Expand visual prompts + Generate Kling clips ===
                logger.info("[3/5] Building detailed Kling prompts...")
                detailed_prompts = prompt_builder.build_prompts(thought.text, thought.visual_scenes)

                num_scenes = len(detailed_prompts)
                logger.info(f"  Generating {num_scenes} Kling video clips (5s each)...")
                video_path = output_dir / f"video_{i:02d}.mp4"

                secs_per_clip = 5  # 5s clips for higher quality

                clip_paths = []
                for s_idx, scene_prompt in enumerate(detailed_prompts):
                    clip_path = output_dir / f"clip_{i:02d}_s{s_idx}.mp4"
                    logger.info(f"  Scene {s_idx + 1}/{num_scenes}: {scene_prompt[:80]}...")
                    kling.generate(scene_prompt, clip_path, duration=secs_per_clip)
                    clip_paths.append(clip_path)

                # Stitch clips
                if len(clip_paths) == 1:
                    clip_paths[0].rename(video_path)
                elif clip_paths:
                    concat_file = output_dir / f"concat_{i:02d}.txt"
                    concat_file.write_text("".join(f"file '{p}'\n" for p in clip_paths))
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(concat_file), "-c", "copy", str(video_path)
                    ], capture_output=True)
                    concat_file.unlink()
                    for p in clip_paths:
                        p.unlink(missing_ok=True)

                # === STEP 4: Merge video + audio + captions ===
                logger.info("[4/5] Merging video + audio + captions...")
                merged_path = output_dir / f"merged_{i:02d}.mp4"
                final_path = output_dir / f"final_{i:02d}.mp4"
                self._merge_video_audio(video_path, audio_path, merged_path, audio_duration)
                self._add_captions(merged_path, final_path, thought.text, audio_duration)
                merged_path.unlink(missing_ok=True)

                # === STEP 5: Telegram approval → Upload ===
                title = thought.hook_text
                description = f"{thought.text}\n\n#shorts #whatif #scifi #mindblown #paralleluniverse #mindrift"
                tags = ["what if", "mind bending", "sci-fi", "time travel", "parallel universe",
                        "alternate history", "simulation theory", "shorts", "mindrift"]

                if dry_run:
                    logger.info(f"  [DRY RUN] Would send for approval: '{title}'")
                else:
                    # Send video to Telegram and wait for approval
                    logger.info("[5/5] Sending video for approval on Telegram...")
                    sent = telegram.send_video_for_approval(str(final_path), thought.text, audio_duration)

                    if not sent:
                        logger.error("  Failed to send video to Telegram")
                        telegram.send_message(f"⚠️ Video generated but couldn't send for preview. Title: {title}")
                        continue

                    approval = telegram.wait_for_approval(timeout_minutes=30)

                    if approval is True:
                        logger.info("  ✅ Approved! Uploading to YouTube...")
                        uploader.run(
                            long_form_path=final_path,
                            short_paths=[],
                            title=title,
                            description=description,
                            tags=tags,
                            thumbnail_path=final_path,
                            dry_run=False,
                        )
                        telegram.send_completion(title, audio_duration)
                    elif approval is False:
                        logger.info("  ❌ Rejected. Skipping upload.")
                    else:
                        logger.info("  ⏰ No response within 30 min. Skipping upload.")

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
            # Notify on failure
            try:
                TelegramBot().send_message(f"❌ Pipeline failed: {str(e)[:200]}")
            except Exception:
                pass

        logger.info(f"\n{'=' * 50}")
        logger.info(f"Pipeline: {summary['status']} | {len(summary['videos'])} videos")
        logger.info(f"{'=' * 50}")

        return summary
