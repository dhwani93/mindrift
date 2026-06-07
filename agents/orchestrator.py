"""Orchestrator — pet drama pipeline using Seedance 2.0 (video + voice in one call)."""

import logging
import subprocess
from datetime import date
from pathlib import Path

import yaml

from agents.comedy_scorer import ComedyScorer
from agents.seedance_video import SeedanceVideoGenerator
from agents.seed_generator import SeedGenerator
from agents.uploader import YouTubeUploader
from utils.preference_learner import save_feedback
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

# Camera angles to cycle through for variety
CAMERA_ANGLES = [
    "Slow push-in, medium shot.",
    "Static wide shot showing full room.",
    "Low angle looking up at character.",
    "Slight orbit around character, medium close-up.",
]


class Orchestrator:
    """Pipeline: seeds → script → Seedance clips (video+voice) → stitch → approve → upload."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _stitch_clips(self, clip_paths: list[Path], output_path: Path) -> None:
        """Concatenate clips into one video."""
        if len(clip_paths) == 1:
            import shutil
            shutil.copy2(clip_paths[0], output_path)
            return

        concat_file = output_path.parent / "concat.txt"
        concat_file.write_text("".join(f"file '{p}'\n" for p in clip_paths))
        self._run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(output_path)
        ], "stitch_clips")
        concat_file.unlink()

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
            seedance = SeedanceVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            # ============================================
            # STEP 1: Get seed
            # ============================================
            logger.info("[1/5] Getting seed...")
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
                logger.info("  Seeds sent. Waiting for reply...")

                import time
                start = time.time()
                while time.time() - start < 1800:
                    msg = telegram.get_latest_message(max_age_hours=0.5)
                    if msg:
                        user_reply = msg
                        break
                    time.sleep(10)

            # Parse
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
            # STEP 2 + GATE 1: Script (retry up to 3x)
            # ============================================
            scored = None
            for attempt in range(3):
                logger.info(f"[2/5] Generating script (attempt {attempt + 1}/3)...")
                scored = scorer.score_and_pick(chosen_seed, modifier=modifier)

                script_msg = (
                    f"📝 Script: \"{chosen_seed.title}\" (attempt {attempt + 1}/3)\n"
                    f"Character: {chosen_seed.character.replace('_', ' ').title()}\n\n"
                    f"---\n{scored.script}\n---\n\n"
                    f"YES to approve, NO + reason to regenerate."
                )
                telegram.send_message(script_msg)

                if dry_run:
                    break

                approved, reason = telegram.wait_for_approval()
                if approved is True:
                    logger.info("  ✅ Script approved!")
                    break
                elif approved is False:
                    save_feedback("script_feedback", reason)
                    if attempt < 2:
                        modifier = (modifier + " " + reason).strip()
                    else:
                        telegram.send_message("⏭️ 3 attempts. Skipping.")
                        summary["status"] = "skipped"
                        return summary
                else:
                    summary["status"] = "skipped"
                    return summary

            # ============================================
            # STEP 3: Build Seedance prompts (4 clips)
            # ============================================
            logger.info("[3/5] Building clip prompts...")

            # Split script into 4 lines for 4 clips
            import re
            clean_script = re.sub(r'\[.*?\]', '', scored.script).strip()
            lines = [l.strip() for l in clean_script.split('\n') if l.strip()]

            # Pad or trim to 4 lines
            while len(lines) < 4:
                lines.append(lines[-1] if lines else "...")
            lines = lines[:4]

            # Build prompts — each clip gets a camera angle + dialogue
            character_desc = {
                "orange_cat": "A fluffy real orange tabby cat with bright green eyes",
                "golden_retriever": "A fluffy real golden retriever with big brown puppy eyes",
                "senior_dog": "A real old gray-muzzled labrador with wise tired eyes and small reading glasses",
                "kitten": "A real tiny gray tabby kitten with enormous round eyes",
            }.get(chosen_seed.character, "A fluffy real orange tabby cat")

            setting = "Cozy apartment living room, warm golden afternoon light through window, bookshelf with plants and books behind, cream couch with throw blanket."

            clip_prompts = []
            for i, line in enumerate(lines):
                camera = CAMERA_ANGLES[i % len(CAMERA_ANGLES)]
                prompt = (
                    f"{camera} {setting} "
                    f"{character_desc} sits on the couch, looking at camera. "
                    f"The cat speaks with expressive face: '{line}' "
                    f"Mouth moves naturally with speech. "
                    f"Photorealistic, cinematic shallow depth of field, warm lighting, 4K."
                )
                clip_prompts.append({"number": i + 1, "line": line, "prompt": prompt})

            # Send prompts for approval
            prompts_msg = "🎬 Video prompts (4 clips × 5s):\n\n"
            for cp in clip_prompts:
                prompts_msg += f"Clip {cp['number']}: \"{cp['line']}\"\n"
                prompts_msg += f"Camera: {CAMERA_ANGLES[(cp['number']-1) % 4]}\n\n"
            prompts_msg += "YES to generate, NO + reason to regenerate."
            telegram.send_message(prompts_msg)

            if not dry_run:
                approved, reason = telegram.wait_for_approval()
                if approved is not True:
                    if reason:
                        save_feedback("prompt_feedback", reason)
                    telegram.send_message("⏭️ Skipped. No credits spent.")
                    summary["status"] = "skipped"
                    return summary
                logger.info("  ✅ Prompts approved! Generating...")

            # ============================================
            # STEP 4: Generate Seedance clips (parallel submission)
            # ============================================
            logger.info("[4/5] Generating 4 Seedance clips...")

            clip_paths = []
            for cp in clip_prompts:
                clip_path = output_dir / f"clip_{cp['number']:02d}.mp4"
                logger.info(f"  Clip {cp['number']}: \"{cp['line'][:40]}...\"")
                seedance.generate(cp["prompt"], clip_path, duration=5)
                clip_paths.append(clip_path)

            # Stitch all clips together
            final_path = output_dir / "final.mp4"
            self._stitch_clips(clip_paths, final_path)
            logger.info(f"  Final video: {final_path}")

            # Clean up individual clips
            for p in clip_paths:
                p.unlink(missing_ok=True)

            # ============================================
            # STEP 5 + GATE 3: Final video approval
            # ============================================
            title = chosen_seed.title
            description = f"{clean_script[:150]}...\n\n#shorts #pets #funny #petcomedy #pawsandopinions"
            tags = self.config["seo"]["default_tags"] + [chosen_seed.character.replace("_", " "), chosen_seed.topic]

            if dry_run:
                logger.info(f"  [DRY RUN] Would send for approval: '{title}'")
            else:
                logger.info("[5/5] Sending final video for approval...")
                sent = telegram.send_video_for_approval(str(final_path), title, clean_script, 20)

                if sent:
                    approved, reason = telegram.wait_for_approval()
                    if approved is True:
                        logger.info("  ✅ Uploading!")
                        uploader.run(
                            long_form_path=final_path, short_paths=[],
                            title=title, description=description,
                            tags=tags, thumbnail_path=final_path, dry_run=False,
                        )
                        telegram.send_completion(title, 20)
                    elif approved is False:
                        save_feedback("video_feedback", reason)
                        logger.info(f"  ❌ Rejected: {reason[:60]}")
                else:
                    logger.error("  Failed to send video")

            summary["videos"].append({"title": title, "duration": 20})
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
