"""Orchestrator — pet-POV comedy video pipeline."""

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
    """Pipeline: seeds → user picks → comedy score → voice → Kling clips → merge → approve → upload."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _add_captions(self, input_path: Path, output_path: Path, captions: list[str], duration: float) -> None:
        """Add caption moments as TikTok-style text overlays."""
        if not captions:
            import shutil
            shutil.copy2(input_path, output_path)
            return

        time_per_caption = duration / len(captions)
        filters = []
        for idx, caption in enumerate(captions):
            start = idx * time_per_caption
            end = (idx + 1) * time_per_caption
            safe = caption.replace("'", "\u2019").replace(":", "\\:").replace("\\", "")
            filters.append(
                f"drawtext=text='{safe}'"
                f":fontsize=48:fontcolor=white:borderw=4:bordercolor=black"
                f":x=(w-text_w)/2:y=h*0.78"
                f":enable='between(t,{start:.2f},{end:.2f})'"
            )

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy", "-pix_fmt", "yuv420p", str(output_path),
        ]
        try:
            self._run_ffmpeg(cmd, "add_captions")
        except RuntimeError as e:
            logger.warning(f"Captions failed: {e}")
            import shutil
            shutil.copy2(input_path, output_path)

    def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path, duration: float) -> None:
        """Merge video with voiceover, trim to exact audio length."""
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
        """Run the full pet-POV pipeline.

        Flow: generate seeds → send to Telegram → user picks → score scripts →
              generate voice → generate Kling clips → merge → approve → upload
        """
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {"date": run_date, "status": "started", "videos": []}

        logger.info(f"{'=' * 50}")
        logger.info(f"MINDRIFT PET POV — {run_date}")
        logger.info(f"{'=' * 50}")

        try:
            seed_gen = SeedGenerator()
            scorer = ComedyScorer()
            voice_gen = VoiceGenerator()
            prompt_builder = KlingPromptBuilder()
            kling = KlingVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            # === STEP 1: Generate and send seeds ===
            logger.info("[1/6] Generating episode seeds...")

            # Check if user already sent a reply (from earlier Telegram)
            user_reply = telegram.get_latest_message(max_age_hours=3)

            if user_reply:
                logger.info(f"  User already replied: {user_reply[:60]}")
            else:
                # Generate seeds and send to Telegram
                seeds = seed_gen.generate_seeds()
                formatted = seed_gen.format_for_telegram(seeds)
                telegram.send_seeds(formatted)
                logger.info("  Seeds sent to Telegram. Waiting for reply (30 min)...")

                # Wait for user reply
                user_reply = None
                # Poll for reply
                import time
                start = time.time()
                while time.time() - start < 1800:  # 30 min
                    msg = telegram.get_latest_message(max_age_hours=0.5)
                    if msg:
                        user_reply = msg
                        break
                    time.sleep(10)

            # === STEP 2: Parse user choice ===
            logger.info("[2/6] Parsing user choice...")

            if user_reply:
                parsed = telegram.parse_seed_reply(user_reply)
                logger.info(f"  Parsed: {parsed}")
            else:
                logger.info("  No reply — using top seed")
                parsed = {"seed_number": 1, "modifier": None, "custom_idea": None}

            # Get or generate the seeds if we don't have them
            if 'seeds' not in dir():
                seeds = seed_gen.generate_seeds()

            if parsed.get("custom_idea"):
                # User typed their own idea — generate a seed from it
                custom_seeds = seed_gen.generate_seeds(bias=parsed["custom_idea"])
                chosen_seed = custom_seeds[0]
            elif parsed.get("seed_number"):
                idx = min(parsed["seed_number"] - 1, len(seeds) - 1)
                chosen_seed = seeds[idx]
            else:
                chosen_seed = seeds[0]

            modifier = parsed.get("modifier", "")
            logger.info(f"  Chosen: '{chosen_seed.title}' ({chosen_seed.character}) | Modifier: {modifier or 'none'}")

            # === STEP 3: Comedy scoring — 3 variants, pick best ===
            logger.info("[3/6] Scoring comedy scripts...")
            scored = scorer.score_and_pick(chosen_seed, modifier=modifier)
            logger.info(f"  Best: {scored.tone} ({scored.total_score}) — {scored.script[:60]}...")

            # === STEP 4: Generate voiceover ===
            logger.info("[4/6] Generating voiceover...")
            clean_script = re.sub(r'\[.*?\]', '', scored.script).strip()

            audio_path = output_dir / "voice.wav"
            raw_path = output_dir / "voice_raw.mp3"

            # Get character-specific voice settings
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

            # Enforce duration range
            min_dur = 15
            max_dur = chosen_seed.recommended_length_sec or self.config["content"]["max_video_duration_sec"]
            if audio_duration < min_dur:
                audio = audio + AudioSegment.silent(duration=int((min_dur - audio_duration) * 1000))
                audio.export(str(audio_path), format="wav")
                audio_duration = min_dur
            if audio_duration > max_dur:
                audio = audio[:max_dur * 1000].fade_out(500)
                audio.export(str(audio_path), format="wav")
                audio_duration = max_dur

            logger.info(f"  Voice: {audio_duration:.1f}s ({chosen_seed.character})")

            # === STEP 5: Generate Kling video clips ===
            logger.info("[5/6] Generating Kling video clips...")
            clips = prompt_builder.build_prompts(
                script=scored.script,
                character=chosen_seed.character,
                visual_direction=scored.visual_direction,
                target_length_sec=chosen_seed.recommended_length_sec,
            )

            video_path = output_dir / "video.mp4"
            clip_paths = []
            for clip in clips:
                clip_path = output_dir / f"clip_{clip['clip_number']:02d}.mp4"
                logger.info(f"  Clip {clip['clip_number']}: {clip['duration_sec']}s — {clip['purpose'][:60]}")
                kling.generate(clip["prompt"], clip_path, duration=clip["duration_sec"])
                clip_paths.append(clip_path)

            # Stitch clips
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

            # Merge video + audio + captions
            logger.info("  Merging video + audio + captions...")
            merged_path = output_dir / "merged.mp4"
            final_path = output_dir / "final.mp4"
            self._merge_video_audio(video_path, audio_path, merged_path, audio_duration)
            self._add_captions(merged_path, final_path, scored.caption_moments, audio_duration)
            merged_path.unlink(missing_ok=True)

            # === STEP 6: Telegram approval → Upload ===
            title = chosen_seed.title
            description = f"{scored.script[:150]}...\n\n#shorts #pets #funny #petcomedy #pawsandopinions"
            tags = self.config["seo"]["default_tags"] + [chosen_seed.character.replace("_", " "), chosen_seed.topic]

            if dry_run:
                logger.info(f"  [DRY RUN] Would send for approval: '{title}'")
            else:
                logger.info("[6/6] Sending for Telegram approval...")
                sent = telegram.send_video_for_approval(str(final_path), title, scored.script, audio_duration)

                if sent:
                    approval = telegram.wait_for_approval(timeout_minutes=30)
                    if approval is True:
                        logger.info("  ✅ Approved! Uploading...")
                        uploader.run(
                            long_form_path=final_path, short_paths=[],
                            title=title, description=description,
                            tags=tags, thumbnail_path=final_path, dry_run=False,
                        )
                        telegram.send_completion(title, audio_duration)
                    elif approval is False:
                        logger.info("  ❌ Rejected.")
                    else:
                        logger.info("  ⏰ No response. Skipping.")
                else:
                    logger.error("  Failed to send video to Telegram")

            summary["videos"].append({
                "title": title,
                "character": chosen_seed.character,
                "tone": scored.tone,
                "duration": audio_duration,
                "score": scored.total_score,
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
