"""Orchestrator — 3-series pet drama pipeline with scriptwriter agent."""

import json
import logging
import re
import subprocess
import time
from datetime import date
from pathlib import Path

import yaml

from agents.scriptwriter import Scriptwriter
from agents.seedance_video import SeedanceVideoGenerator
from agents.seed_generator import SeedGenerator
from agents.uploader import YouTubeUploader
from utils.preference_learner import save_feedback
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
SERIES_TRACKER_PATH = Path(__file__).parent.parent / "data" / "series_tracker.json"
DAILY_MODE_PATH = Path(__file__).parent.parent / "data" / "daily_mode.json"

CHAR_DESCS = {
    "orange_cat": "a fluffy real orange tabby cat with bright green eyes",
    "white_cat": "a sleek real white cat with blue eyes",
    "golden_retriever": "a fluffy real golden retriever with big brown puppy eyes",
    "senior_dog": "a real old gray-muzzled labrador with wise tired eyes",
    "kitten": "a real tiny gray tabby kitten with enormous round eyes",
}

# Series definitions
SERIES = {
    "office_drama": {"char1": "orange_cat", "char2": "white_cat"},
    "couple_drama": {"char1": "orange_cat", "char2": "golden_retriever"},
    "roommates": {"char1": "senior_dog", "char2": "kitten"},
}


def load_daily_mode() -> dict:
    """Load today's mode. Returns empty dict if no mode set or different day."""
    if DAILY_MODE_PATH.exists():
        data = json.loads(DAILY_MODE_PATH.read_text())
        if data.get("date") == date.today().isoformat():
            return data
    return {}


def save_daily_mode(mode: str, morning_pick: str, series_key: str | None, used_series: list[str]) -> None:
    """Save today's mode decision."""
    DAILY_MODE_PATH.write_text(json.dumps({
        "date": date.today().isoformat(),
        "mode": mode,  # "series" or "standalone"
        "morning_pick": morning_pick,
        "morning_series_key": series_key,
        "used_series": used_series,
    }, indent=2))


def add_used_series(series_key: str) -> None:
    """Mark a series as used today."""
    mode = load_daily_mode()
    if mode:
        used = mode.get("used_series", [])
        if series_key not in used:
            used.append(series_key)
            mode["used_series"] = used
            DAILY_MODE_PATH.write_text(json.dumps(mode, indent=2))


def load_tracker() -> dict:
    if SERIES_TRACKER_PATH.exists():
        return json.loads(SERIES_TRACKER_PATH.read_text())
    return {}


def save_tracker(tracker: dict) -> None:
    SERIES_TRACKER_PATH.write_text(json.dumps(tracker, indent=2))


def get_unused_series_today() -> str | None:
    """Get a series not yet used today."""
    tracker = load_tracker()
    today = date.today().isoformat()
    for key in SERIES:
        series = tracker.get(key, {})
        if series.get("last_run_date") != today:
            return key
    return None


def mark_series_used(series_key: str, title: str) -> None:
    """Mark a series as used today and increment episode."""
    tracker = load_tracker()
    if series_key not in tracker:
        tracker[series_key] = {"episode_count": 0, "last_episode_title": None}
    tracker[series_key]["episode_count"] += 1
    tracker[series_key]["last_episode_title"] = title
    tracker[series_key]["last_run_date"] = date.today().isoformat()
    save_tracker(tracker)


class Orchestrator:
    """Pipeline with morning (interactive) and auto modes."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _add_title(self, input_path: Path, output_path: Path, title: str) -> None:
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

    def _wait_for_number(self, telegram: TelegramBot, valid: list[str], timeout_min: int = 15) -> str | None:
        """Wait for a number reply. Flushes old messages first."""
        import requests
        try:
            r = requests.get(f"{telegram.base_url}/getUpdates", timeout=10)
            if r.json().get("result"):
                last_id = r.json()["result"][-1]["update_id"]
                requests.get(f"{telegram.base_url}/getUpdates", params={"offset": last_id + 1}, timeout=10)
        except Exception:
            pass

        start = time.time()
        while time.time() - start < timeout_min * 60:
            try:
                r = requests.get(f"{telegram.base_url}/getUpdates", params={"timeout": 30}, timeout=40)
                for update in r.json().get("result", []):
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == str(telegram.chat_id):
                        text = msg.get("text", "").strip()
                        requests.get(f"{telegram.base_url}/getUpdates", params={"offset": update["update_id"] + 1}, timeout=10)
                        if text in valid:
                            return text
            except Exception:
                pass
            time.sleep(5)
        return None

    def _build_seedance_prompt(self, script, setting: str) -> str:
        """Build a multi-shot Seedance prompt from script lines."""
        cameras = ["Close-up", "Medium wide shot", "Low angle close-up", "Pull-back wide shot"]
        shots = []

        for i, line_data in enumerate(script.lines):
            speaker = line_data["speaker"]
            dialogue = line_data["line"]
            char_desc = CHAR_DESCS.get(speaker, "a fluffy real cat")
            cam = cameras[i % len(cameras)]
            shots.append(
                f"Shot {i+1}: {cam} of {char_desc} in {setting}. "
                f"Character speaks: '{dialogue}'"
            )

        prompt = " ".join(shots)
        prompt += " No background music. No sound effects. Only character dialogue. Photorealistic, cinematic, warm lighting, 4K."
        return prompt

    def run_daily(self, run_date: str | None = None, dry_run: bool = False, slot: str = "morning") -> dict:
        """Run pipeline.

        Args:
            slot: "morning" (interactive), "midday" (auto), "evening" (auto)
        """
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date / slot
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {"date": run_date, "slot": slot, "status": "started", "videos": []}

        logger.info(f"{'=' * 50}")
        logger.info(f"PAWS & OPINIONS — {run_date} [{slot.upper()}]")
        logger.info(f"{'=' * 50}")

        try:
            seed_gen = SeedGenerator()
            writer = Scriptwriter()
            seedance = SeedanceVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            # ============================================
            # STEP 1: Pick topic (morning decides the day)
            # ============================================
            chosen_seed = None
            series_key = ""

            # Check for file seed (workflow_dispatch)
            seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"
            file_seed = ""
            if seed_file.exists():
                file_seed = seed_file.read_text().strip()
                if file_seed:
                    seed_file.write_text("")

            if file_seed:
                logger.info(f"[1/5] Direct seed: {file_seed[:60]}")
                seeds = seed_gen.generate_seeds(bias=file_seed)
                chosen_seed = seeds[0]

            elif slot == "morning":
                # Interactive — send 5 options, wait 15 min
                logger.info("[1/5] Sending topic options...")
                seeds = seed_gen.generate_seeds()
                categories = ["💼 OFFICE", "💕 COUPLE", "🏠 ROOMMATES", "📰 TRENDING", "🤪 WILD CARD"]
                topic_msg = "🐾 Pick a topic (this decides the whole day):\n\n"
                topic_msg += "Pick 1-3 → today is SERIES day (all 3 series run)\nPick 4-5 → today is STANDALONE day (no series)\n\n"
                for i, s in enumerate(seeds[:5]):
                    cat = categories[i] if i < len(categories) else "🎲"
                    char2_label = f"+{s.character_2.replace('_',' ')}" if s.character_2 != "none" else " solo"
                    topic_msg += f"{i+1}. {cat} {s.title} ({s.character.replace('_',' ')}{char2_label})\n\"{s.hook}\"\n📍 {s.setting[:40]}\n\n"
                topic_msg += "6. ✍️ YOUR IDEA\n\nReply 1-6 (15 min, then auto-picks #1)."
                telegram.send_message(topic_msg)

                if not dry_run:
                    pick = self._wait_for_number(telegram, ["1", "2", "3", "4", "5"], timeout_min=15)
                    if pick:
                        chosen_seed = seeds[int(pick) - 1]
                        logger.info(f"  Picked: {pick} — {chosen_seed.title}")
                    else:
                        chosen_seed = seeds[0]
                        logger.info("  No response — auto-picked #1")
                else:
                    chosen_seed = seeds[0]

                # Determine if series or standalone based on pick
                is_series = False
                for sk, chars in SERIES.items():
                    if chosen_seed.character == chars["char1"] and chosen_seed.character_2 == chars["char2"]:
                        series_key = sk
                        is_series = True
                        break

                # Save daily mode — this decides midday + evening behavior
                if is_series:
                    save_daily_mode("series", chosen_seed.title, series_key, [series_key])
                    telegram.send_message(f"📅 Series day! 1PM + 6PM will auto-run the other two series.")
                else:
                    save_daily_mode("standalone", chosen_seed.title, None, [])
                    telegram.send_message(f"📅 Standalone day! 1PM + 6PM will be trending/wildcard content.")

            else:
                # Auto mode (midday/evening) — read morning's decision
                logger.info(f"[1/5] Auto-mode for {slot}...")
                daily = load_daily_mode()

                if not daily:
                    # No morning run happened — do standalone
                    logger.info("  No morning run found — standalone mode")
                    seeds = seed_gen.generate_seeds(bias="trending current events")
                    chosen_seed = seeds[0]

                elif daily.get("mode") == "series":
                    # Series day — pick an unused series
                    used = daily.get("used_series", [])
                    available = [sk for sk in SERIES if sk not in used]

                    if available:
                        series_key = available[0]
                        series_info = SERIES[series_key]
                        seeds = seed_gen.generate_seeds(bias=series_key.replace("_", " "))
                        chosen_seed = seeds[0]
                        chosen_seed.character = series_info["char1"]
                        chosen_seed.character_2 = series_info["char2"]
                        add_used_series(series_key)
                        logger.info(f"  Series day — auto-picked: {series_key}")
                    else:
                        # All 3 series done — standalone fallback
                        seeds = seed_gen.generate_seeds(bias="trending")
                        chosen_seed = seeds[0]
                        logger.info("  All series used — standalone fallback")

                else:
                    # Standalone day — do trending/wildcard
                    seeds = seed_gen.generate_seeds(bias="trending current events wildcard")
                    chosen_seed = seeds[0]
                    logger.info(f"  Standalone day — trending: {chosen_seed.title}")

            # Determine series key if not set
            if not series_key:
                for sk, chars in SERIES.items():
                    if chosen_seed.character == chars["char1"] and chosen_seed.character_2 == chars["char2"]:
                        series_key = sk
                        break

            logger.info(f"  Topic: '{chosen_seed.title}' | Series: {series_key or 'standalone'}")

            # ============================================
            # STEP 2: Write script (3 options → user picks or auto)
            # ============================================
            logger.info("[2/5] Writing scripts...")
            options = writer.write_three_options(
                topic=chosen_seed.premise or chosen_seed.title,
                character_1=chosen_seed.character,
                character_2=chosen_seed.character_2 if chosen_seed.character_2 != "none" else chosen_seed.character,
                setting=chosen_seed.setting,
                series_key=series_key,
                duration=15,
            )

            if slot == "morning" and not dry_run:
                # Send 3 options, wait 15 min
                script_msg = writer.format_for_telegram(options)
                telegram.send_message(script_msg)

                pick = self._wait_for_number(telegram, ["1", "2", "3"], timeout_min=15)
                if pick:
                    chosen_script = options[int(pick) - 1]
                    logger.info(f"  Picked script: {pick}")
                else:
                    chosen_script = options[0]
                    logger.info("  No response — auto-picked script #1")
            else:
                # Auto mode — pick the first (highest quality) option
                chosen_script = options[0]
                logger.info(f"  Auto-picked script: {chosen_script.title}")

            # ============================================
            # STEP 3: Build Seedance prompt + show summary
            # ============================================
            logger.info("[3/5] Building video prompt...")
            setting = chosen_seed.setting or "cozy apartment, warm lighting"
            seedance_prompt = self._build_seedance_prompt(chosen_script, setting)

            # Show summary
            summary_msg = f"🎬 Video: \"{chosen_script.title}\"\n\n"
            for line in chosen_script.lines:
                summary_msg += f"{line['speaker'].replace('_',' ').title()}: \"{line['line']}\"\n"
            summary_msg += f"\n📍 {setting[:50]}\n\nReply YES to generate, NO to skip."
            telegram.send_message(summary_msg)

            if not dry_run:
                approved, reason = telegram.wait_for_approval()
                if approved is not True:
                    if reason:
                        save_feedback("prompt_feedback", reason)
                    telegram.send_message("⏭️ Skipped. No credits spent.")
                    summary["status"] = "skipped"
                    return summary
                logger.info("  ✅ Approved!")

            # ============================================
            # STEP 4: Generate Seedance video
            # ============================================
            logger.info("[4/5] Generating 15s Seedance video...")
            clip_path = output_dir / "clip.mp4"
            seedance.generate(seedance_prompt, clip_path, duration=15)

            # Add title
            final_path = output_dir / "final.mp4"
            self._add_title(clip_path, final_path, chosen_script.title)
            clip_path.unlink(missing_ok=True)

            # ============================================
            # STEP 5: Final approval + upload
            # ============================================
            title = chosen_script.title
            dialogue_text = " | ".join(f"{l['line']}" for l in chosen_script.lines[:3])
            description = f"{dialogue_text}...\n\n#shorts #pets #funny #petcomedy #pawsandopinions"
            tags = self.config["seo"]["default_tags"] + [chosen_seed.character.replace("_", " ")]

            if dry_run:
                logger.info(f"  [DRY RUN] Would send: '{title}'")
            else:
                logger.info("[5/5] Sending final video for approval...")
                sent = telegram.send_video_for_approval(str(final_path), title, dialogue_text, 15)

                if sent:
                    approved, reason = telegram.wait_for_approval()
                    if approved is True:
                        logger.info("  ✅ Uploading!")
                        uploader.run(
                            long_form_path=final_path, short_paths=[],
                            title=title, description=description,
                            tags=tags, thumbnail_path=final_path, dry_run=False,
                        )
                        telegram.send_completion(title, 15)
                        # Track series
                        if series_key:
                            mark_series_used(series_key, title)
                    elif approved is False:
                        save_feedback("video_feedback", reason)
                        logger.info(f"  ❌ Rejected: {reason[:60]}")
                else:
                    logger.error("  Failed to send video")

            summary["videos"].append({"title": title, "series": series_key, "slot": slot})
            summary["status"] = "success"

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            summary["status"] = "failed"
            summary["error"] = str(e)
            try:
                TelegramBot().send_message(f"❌ [{slot}] Pipeline failed: {str(e)[:200]}")
            except Exception:
                pass

        logger.info(f"\n{'=' * 50}")
        logger.info(f"Pipeline: {summary['status']} [{slot}]")
        logger.info(f"{'=' * 50}")
        return summary
