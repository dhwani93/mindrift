"""Orchestrator — 5-step pet drama pipeline with Seedance 2.0."""

import logging
import re
import subprocess
import time
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

CAMERA_ANGLES = [
    "Slow push-in, medium shot.",
    "Static wide shot showing full room.",
    "Low angle looking up at character.",
    "Slight orbit around character, medium close-up.",
]


class Orchestrator:
    """5-step pipeline: topics → scripts → prompts → generate → approve → upload."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _add_title(self, input_path: Path, output_path: Path, title: str) -> None:
        """Add persistent 2-3 word title at top of video with Bebas Neue font."""
        safe_title = title.upper().replace("'", "\u2019").replace(":", "\\:")
        font_path = Path(__file__).parent.parent / "assets" / "fonts" / "BebasNeue-Regular.ttf"
        font_arg = f":fontfile={font_path}" if font_path.exists() else ""

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", (
                f"drawtext=text='{safe_title}'{font_arg}"
                f":fontsize=72:fontcolor=white"
                f":borderw=5:bordercolor=black"
                f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
                f":x=(w-text_w)/2:y=h*0.06"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        try:
            self._run_ffmpeg(cmd, "add_title")
        except RuntimeError:
            import shutil
            shutil.copy2(input_path, output_path)

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

    def _wait_for_number(self, telegram: TelegramBot, valid: list[str], timeout_hours: int = 6) -> str | None:
        """Wait for user to reply with a specific number. Returns the number or None.

        Clears old messages first so we only read NEW replies.
        """
        import requests
        # Flush all existing updates so we only get fresh replies
        try:
            r = requests.get(f"{telegram.base_url}/getUpdates", timeout=10)
            if r.json().get("result"):
                last_id = r.json()["result"][-1]["update_id"]
                requests.get(f"{telegram.base_url}/getUpdates", params={"offset": last_id + 1}, timeout=10)
        except Exception:
            pass

        start = time.time()
        while time.time() - start < timeout_hours * 3600:
            try:
                r = requests.get(f"{telegram.base_url}/getUpdates", params={"timeout": 30}, timeout=40)
                for update in r.json().get("result", []):
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == str(telegram.chat_id):
                        text = msg.get("text", "").strip()
                        # Mark as read
                        requests.get(f"{telegram.base_url}/getUpdates", params={"offset": update["update_id"] + 1}, timeout=10)
                        if text in valid:
                            return text
            except Exception:
                pass
            time.sleep(5)
        return None

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
            # STEP 1: Topic selection (3 options)
            # ============================================
            logger.info("[1/5] Generating topic options...")

            # Check for pre-set seed (from workflow_dispatch)
            seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"
            file_seed = ""
            if seed_file.exists():
                file_seed = seed_file.read_text().strip()
                if file_seed:
                    seed_file.write_text("")

            if file_seed:
                # Direct seed from workflow input — skip topic selection
                logger.info(f"  Direct seed: {file_seed[:60]}")
                seeds = seed_gen.generate_seeds(bias=file_seed)
                chosen_seed = seeds[0]
            else:
                # Generate 5 topics, send to Telegram, wait for pick
                seeds = seed_gen.generate_seeds()
                categories = ["📰 TRENDING", "💕 SAUCY", "🐾 PET CLASSIC", "🤪 WILD CARD", "📰 TRENDING"]
                topic_msg = "🐾 Pick a topic:\n\n"
                for i, s in enumerate(seeds[:5]):
                    cat = categories[i] if i < len(categories) else "🎲"
                    topic_msg += f"{i + 1}. {cat} {s.title}\n\"{s.hook}\"\n\n"
                topic_msg += "Reply 1-5."
                telegram.send_message(topic_msg)

                if not dry_run:
                    pick = self._wait_for_number(telegram, ["1", "2", "3", "4", "5"])
                    if pick:
                        chosen_seed = seeds[int(pick) - 1]
                        logger.info(f"  User picked topic {pick}: {chosen_seed.title}")
                    else:
                        chosen_seed = seeds[0]
                        logger.info("  No response — using topic 1")
                else:
                    chosen_seed = seeds[0]

            logger.info(f"  Topic: '{chosen_seed.title}' ({chosen_seed.character})")

            # ============================================
            # STEP 2: Script selection (3 options)
            # ============================================
            logger.info("[2/5] Generating 3 script options...")
            options = scorer.generate_options(chosen_seed)

            script_msg = f"📝 Pick a script for \"{chosen_seed.title}\":\n\n"
            for i, opt in enumerate(options):
                script_msg += f"{i + 1}. [{opt.tone.upper()}]\n{opt.script}\n\n"
            script_msg += "Reply 1, 2, or 3."
            telegram.send_message(script_msg)

            if not dry_run:
                pick = self._wait_for_number(telegram, ["1", "2", "3"])
                if pick:
                    scored = options[int(pick) - 1]
                    logger.info(f"  User picked script {pick}: {scored.tone}")
                else:
                    scored = options[0]
                    logger.info("  No response — using script 1")
            else:
                scored = options[0]

            # ============================================
            # STEP 3: Build prompts + show for approval
            # ============================================
            logger.info("[3/5] Building clip prompts...")

            clean_script = re.sub(r'\[.*?\]', '', scored.script).strip()

            # Split into 4 sentences
            raw_lines = re.split(r'[.!?\n]+', clean_script)
            lines = [l.strip() for l in raw_lines if l.strip() and len(l.strip()) > 3]

            if len(lines) < 4:
                words = clean_script.split()
                chunk_size = max(1, len(words) // 4)
                lines = []
                for i in range(0, len(words), chunk_size):
                    lines.append(" ".join(words[i:i + chunk_size]))

            lines = lines[:4]
            while len(lines) < 4:
                lines.append(lines[-1] if lines else "...")

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
                    f"The cat speaks clearly and naturally: '{line}' "
                    f"Mouth moves with speech. No background music. No sound effects. Only dialogue. "
                    f"Photorealistic, cinematic shallow depth of field, warm lighting, 4K."
                )
                clip_prompts.append({"number": i + 1, "line": line, "prompt": prompt})

            # Show prompts
            prompts_msg = "🎬 Video plan (4 clips × 5s):\n\n"
            for cp in clip_prompts:
                prompts_msg += f"Clip {cp['number']}: \"{cp['line']}\"\nCamera: {CAMERA_ANGLES[(cp['number']-1) % 4]}\n\n"
            prompts_msg += "Reply YES to generate, NO to skip."
            telegram.send_message(prompts_msg)

            if not dry_run:
                approved, reason = telegram.wait_for_approval()
                if approved is not True:
                    if reason:
                        save_feedback("prompt_feedback", reason)
                    telegram.send_message("⏭️ Skipped. No credits spent.")
                    summary["status"] = "skipped"
                    return summary
                logger.info("  ✅ Prompts approved!")

            # ============================================
            # STEP 4: Generate Seedance clips
            # ============================================
            logger.info("[4/5] Generating 4 Seedance clips...")

            clip_paths = []
            for cp in clip_prompts:
                clip_path = output_dir / f"clip_{cp['number']:02d}.mp4"
                logger.info(f"  Clip {cp['number']}: \"{cp['line'][:40]}\"")
                seedance.generate(cp["prompt"], clip_path, duration=7)
                clip_paths.append(clip_path)

            # Stitch + title
            stitched_path = output_dir / "stitched.mp4"
            final_path = output_dir / "final.mp4"
            self._stitch_clips(clip_paths, stitched_path)
            self._add_title(stitched_path, final_path, chosen_seed.title)
            stitched_path.unlink(missing_ok=True)
            for p in clip_paths:
                p.unlink(missing_ok=True)

            logger.info(f"  Final video: {final_path}")

            # ============================================
            # STEP 5: Final video approval + upload
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
