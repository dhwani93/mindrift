"""Orchestrator — pet-POV comedy pipeline with multi-step Telegram approval."""

import logging
import re
import subprocess
from datetime import date
from pathlib import Path

import yaml

from agents.comedy_scorer import ComedyScorer
from agents.kling_prompt_builder import KlingPromptBuilder
from agents.kling_video import KlingVideoGenerator
from agents.seed_generator import SeedGenerator
from agents.uploader import YouTubeUploader
from agents.voice_generator import VoiceGenerator
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


class Orchestrator:
    """Pipeline with 3 approval gates before spending money."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _add_text_overlays(self, input_path: Path, output_path: Path, title: str, script: str, duration: float) -> None:
        """Add title at top (persistent) + word-by-word subtitles at bottom."""
        safe_title = title.replace("'", "\u2019").replace(":", "\\:").replace("\\", "")

        # Strip ALL markers before building captions
        clean_script = re.sub(r'\[PAUSE[^\]]*\]', '', script)
        clean_script = re.sub(r'\[.*?\]', '', clean_script)
        clean_script = re.sub(r'\s+', ' ', clean_script).strip()
        words = clean_script.split()
        chunk_size = 3
        chunks = []
        for j in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[j:j + chunk_size]))

        # Build filter chain
        filters = []

        # Title at top — visible entire video
        filters.append(
            f"drawtext=text='{safe_title}'"
            ":fontsize=52:fontcolor=white:borderw=4:bordercolor=black"
            ":x=(w-text_w)/2:y=h*0.08"
        )

        # Subtitles at bottom — word by word
        if chunks:
            time_per_chunk = duration / len(chunks)
            for idx, chunk in enumerate(chunks):
                start = idx * time_per_chunk
                end = (idx + 1) * time_per_chunk
                safe_chunk = chunk.replace("'", "\u2019").replace(":", "\\:").replace("\\", "")
                filters.append(
                    f"drawtext=text='{safe_chunk}'"
                    f":fontsize=44:fontcolor=yellow:borderw=3:bordercolor=black"
                    f":x=(w-text_w)/2:y=h*0.82"
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
            self._run_ffmpeg(cmd, "text_overlays")
        except RuntimeError as e:
            logger.warning(f"Text overlays failed: {e}")
            import shutil
            shutil.copy2(input_path, output_path)

    def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path, duration: float) -> None:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path), "-i", str(audio_path),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, "merge_video_audio")

    def run_daily(self, run_date: str | None = None, dry_run: bool = False) -> dict:
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {"date": run_date, "status": "started", "videos": []}

        logger.info(f"{'=' * 50}")
        logger.info(f"PAWS & OPINIONS — {run_date}")
        logger.info(f"{'=' * 50}")

        try:
            seed_gen = SeedGenerator()
            scorer = ComedyScorer()
            voice_gen = VoiceGenerator()
            prompt_builder = KlingPromptBuilder()
            kling = KlingVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            # ============================================
            # GATE 0: Get seed (file → Telegram → generate)
            # ============================================
            logger.info("[1/7] Getting seed...")
            user_reply = None
            seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"

            if seed_file.exists():
                file_seed = seed_file.read_text().strip()
                if file_seed:
                    seed_file.write_text("")
                    user_reply = file_seed
                    logger.info(f"  Seed from file: {user_reply[:60]}")

            if not user_reply:
                user_reply = telegram.get_latest_message(max_age_hours=3)
                if user_reply:
                    logger.info(f"  Seed from Telegram: {user_reply[:60]}")

            if not user_reply:
                seeds = seed_gen.generate_seeds()
                formatted = seed_gen.format_for_telegram(seeds)
                telegram.send_seeds(formatted)
                logger.info("  Seeds sent. Waiting 30 min...")

                import time
                start = time.time()
                while time.time() - start < 1800:
                    msg = telegram.get_latest_message(max_age_hours=0.5)
                    if msg:
                        user_reply = msg
                        break
                    time.sleep(10)

            # Parse choice
            if user_reply:
                parsed = telegram.parse_seed_reply(user_reply)
            else:
                parsed = {"seed_number": 1, "modifier": None, "custom_idea": None}

            if "seeds" not in dir():
                seeds = seed_gen.generate_seeds(bias=user_reply if parsed.get("custom_idea") else "")

            if parsed.get("custom_idea"):
                custom_seeds = seed_gen.generate_seeds(bias=parsed["custom_idea"])
                chosen_seed = custom_seeds[0]
            elif parsed.get("seed_number"):
                idx = min(parsed["seed_number"] - 1, len(seeds) - 1)
                chosen_seed = seeds[idx]
            else:
                chosen_seed = seeds[0]

            modifier = parsed.get("modifier", "")
            logger.info(f"  Chosen: '{chosen_seed.title}' ({chosen_seed.character})")

            # ============================================
            # STEP 2 + GATE 1: Generate script → approve (retries up to 3x)
            # ============================================
            scored = None
            for attempt in range(3):
                logger.info(f"[2/7] Generating script (attempt {attempt + 1}/3)...")
                scored = scorer.score_and_pick(chosen_seed, modifier=modifier)
                logger.info(f"  Best: {scored.tone} ({scored.total_score})")

                script_msg = (
                    f"📝 Script for: \"{chosen_seed.title}\" (attempt {attempt + 1}/3)\n"
                    f"Character: {chosen_seed.character.replace('_', ' ').title()}\n"
                    f"Tone: {scored.tone}\n\n"
                    f"---\n"
                    f"{scored.script}\n"
                    f"---\n\n"
                    f"Reply YES to approve, NO to regenerate."
                )
                telegram.send_message(script_msg)

                if dry_run:
                    break

                script_approval = telegram.wait_for_approval(timeout_minutes=60)
                if script_approval is True:
                    logger.info("  ✅ Script approved!")
                    break
                elif script_approval is False:
                    logger.info(f"  ❌ Script rejected. {'Regenerating...' if attempt < 2 else 'Last attempt used.'}")
                    if attempt < 2:
                        telegram.send_message("🔄 Regenerating script...")
                        modifier = (modifier + " make it funnier and more relatable").strip()
                    else:
                        telegram.send_message("⏭️ 3 attempts used. Skipping today.")
                        summary["status"] = "skipped"
                        return summary
                else:
                    telegram.send_message("⏰ No response. Skipping.")
                    summary["status"] = "skipped"
                    return summary

            # ============================================
            # STEP 4 + GATE 2: Generate Kling prompts → approve (retries up to 3x)
            # ============================================
            clips = None
            target_length = self.config["content"]["max_video_duration_sec"]
            forced_duration = 5 if target_length <= 20 else 10

            for attempt in range(3):
                logger.info(f"[4/7] Building Kling prompts (attempt {attempt + 1}/3)...")
                clips = prompt_builder.build_prompts(
                    script=scored.script,
                    character=chosen_seed.character,
                    visual_direction=scored.visual_direction,
                    target_length_sec=target_length,
                )

                prompts_msg = f"🎬 Video prompts (attempt {attempt + 1}/3):\n\n"
                for c in clips:
                    prompts_msg += f"Clip {c['clip_number']} ({forced_duration}s): {c['purpose']}\n"
                    prompts_msg += f"→ {c['prompt'][:300]}...\n\n"
                prompts_msg += "Reply YES to generate, NO to regenerate."
                telegram.send_message(prompts_msg)

                if dry_run:
                    break

                prompts_approval = telegram.wait_for_approval(timeout_minutes=60)
                if prompts_approval is True:
                    logger.info("  ✅ Prompts approved! Spending credits now...")
                    break
                elif prompts_approval is False:
                    logger.info(f"  ❌ Prompts rejected. {'Regenerating...' if attempt < 2 else 'Last attempt.'}")
                    if attempt < 2:
                        telegram.send_message("🔄 Regenerating prompts with more detail...")
                    else:
                        telegram.send_message("⏭️ 3 attempts used. No credits spent. Skipping.")
                        summary["status"] = "skipped"
                        return summary
                else:
                    telegram.send_message("⏰ No response. Skipping.")
                    summary["status"] = "skipped"
                    return summary

            # ============================================
            # STEP 6: GENERATE (costs money — only after 2 approvals)
            # ============================================
            logger.info("[6/7] Generating voice + video (approved)...")

            # Voice
            clean_script = re.sub(r'\[.*?\]', '', scored.script).strip()
            audio_path = output_dir / "voice.wav"
            raw_path = output_dir / "voice_raw.mp3"

            char_config = self.config["voice"]["characters"].get(chosen_seed.character, {})
            voice_id = char_config.get("voice_id", "JBFqnCBsd6RMkjVDRZzb")
            voice_gen._generate_audio_with_voice(clean_script, raw_path, voice_id, char_config)

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

            min_dur = 15
            max_dur = self.config["content"]["max_video_duration_sec"]
            if audio_duration < min_dur:
                audio = audio + AudioSegment.silent(duration=int((min_dur - audio_duration) * 1000))
                audio.export(str(audio_path), format="wav")
                audio_duration = min_dur
            if audio_duration > max_dur:
                audio = audio[:max_dur * 1000].fade_out(500)
                audio.export(str(audio_path), format="wav")
                audio_duration = max_dur

            logger.info(f"  Voice: {audio_duration:.1f}s")

            # Kling clips
            video_path = output_dir / "video.mp4"
            clip_paths = []
            for clip in clips:
                clip_path = output_dir / f"clip_{clip['clip_number']:02d}.mp4"
                logger.info(f"  Clip {clip['clip_number']}: {forced_duration}s")
                kling.generate(clip["prompt"], clip_path, duration=forced_duration)
                clip_paths.append(clip_path)

            # Stitch
            if len(clip_paths) == 1:
                clip_paths[0].rename(video_path)
            elif clip_paths:
                concat_file = output_dir / "concat.txt"
                concat_file.write_text("".join(f"file '{p}'\n" for p in clip_paths))
                subprocess.run([
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat_file), "-c", "copy", str(video_path)
                ], capture_output=True)
                concat_file.unlink()
                for p in clip_paths:
                    p.unlink(missing_ok=True)

            # Merge video + audio, then add title + subtitles
            merged_path = output_dir / "merged.mp4"
            final_path = output_dir / "final.mp4"
            self._merge_video_audio(video_path, audio_path, merged_path, audio_duration)
            self._add_text_overlays(merged_path, final_path, chosen_seed.title, clean_script, audio_duration)
            merged_path.unlink(missing_ok=True)

            # ============================================
            # GATE 3: APPROVE FINAL VIDEO
            # ============================================
            title = chosen_seed.title
            description = f"{scored.script[:150]}...\n\n#shorts #pets #funny #petcomedy #pawsandopinions"
            tags = self.config["seo"]["default_tags"] + [chosen_seed.character.replace("_", " "), chosen_seed.topic]

            if dry_run:
                logger.info(f"  [DRY RUN] Would send for approval: '{title}'")
            else:
                logger.info("[7/7] Sending final video for approval...")
                sent = telegram.send_video_for_approval(str(final_path), title, scored.script, audio_duration)

                if sent:
                    approval = telegram.wait_for_approval(timeout_minutes=30)
                    if approval is True:
                        logger.info("  ✅ Uploading!")
                        uploader.run(
                            long_form_path=final_path, short_paths=[],
                            title=title, description=description,
                            tags=tags, thumbnail_path=final_path, dry_run=False,
                        )
                        telegram.send_completion(title, audio_duration)
                    elif approval is False:
                        logger.info("  ❌ Rejected.")
                    else:
                        logger.info("  ⏰ Timed out.")
                else:
                    logger.error("  Failed to send video")

            summary["videos"].append({
                "title": title,
                "character": chosen_seed.character,
                "duration": audio_duration,
            })
            summary["status"] = "success"

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            summary["status"] = "failed"
            summary["error"] = str(e)
            try:
                TelegramBot().send_message(f"❌ Pipeline failed: {str(e)[:200]}")
            except Exception:
                pass

        logger.info(f"\n{'=' * 50}")
        logger.info(f"Pipeline: {summary['status']}")
        logger.info(f"{'=' * 50}")
        return summary
