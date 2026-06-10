"""Orchestrator — 3 connected scenes per day in Luna's Life."""

import json
import logging
import subprocess
import time
from datetime import date
from pathlib import Path

import yaml

from agents.scriptwriter import Scriptwriter, load_character_bible
from agents.seedance_video import SeedanceVideoGenerator
from agents.seed_generator import SeedGenerator
from agents.uploader import YouTubeUploader
from utils.preference_learner import save_feedback
from utils.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
TRACKER_PATH = Path(__file__).parent.parent / "data" / "series_tracker.json"
DAILY_STORY_PATH = Path(__file__).parent.parent / "data" / "daily_story.json"
CHARACTER_BIBLE_PATH = Path(__file__).parent.parent / "data" / "character_bible.json"


def load_tracker() -> dict:
    if TRACKER_PATH.exists():
        return json.loads(TRACKER_PATH.read_text())
    return {"global_episode_count": 0, "last_topics": []}


def save_tracker(tracker: dict) -> None:
    TRACKER_PATH.write_text(json.dumps(tracker, indent=2))


def load_daily_story() -> dict:
    if DAILY_STORY_PATH.exists():
        data = json.loads(DAILY_STORY_PATH.read_text())
        if data.get("date") == date.today().isoformat():
            return data
    return {}


def save_daily_story(story: dict) -> None:
    DAILY_STORY_PATH.write_text(json.dumps(story, indent=2))


def get_char_visual(char_key: str) -> str:
    if CHARACTER_BIBLE_PATH.exists():
        bible = json.loads(CHARACTER_BIBLE_PATH.read_text())
        for pool in ["characters", "season_2_characters", "season_3_characters"]:
            if char_key in bible.get(pool, {}):
                return bible[pool][char_key].get("visual", char_key.replace("_", " "))
    return char_key.replace("_", " ")


class Orchestrator:
    """3 scenes per day: incident → venting → aftermath."""

    def __init__(self, output_base: Path | None = None):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.output_base = output_base or Path(__file__).parent.parent / "output"

    def _run_ffmpeg(self, cmd: list[str], desc: str = "") -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed ({desc}): {result.stderr[-200:]}")

    def _add_title(self, input_path: Path, output_path: Path, title: str, ep_num: int = 0) -> None:
        safe_title = title.upper().replace("'", "\u2019").replace(":", "\\:")
        font_path = Path(__file__).parent.parent / "assets" / "fonts" / "BebasNeue-Regular.ttf"
        font_arg = f":fontfile={font_path}" if font_path.exists() else ""
        filters = (
            f"drawtext=text='{safe_title}'{font_arg}"
            f":fontsize=72:fontcolor=white:borderw=5:bordercolor=black"
            f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
            f":x=(w-text_w)/2:y=h*0.06"
        )
        if ep_num > 0:
            filters += (
                f",drawtext=text='EP.{ep_num}'{font_arg}"
                f":fontsize=28:fontcolor=white@0.7:borderw=2:bordercolor=black@0.5"
                f":x=30:y=h*0.92"
            )
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", filters,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy", "-pix_fmt", "yuv420p", str(output_path),
        ]
        try:
            self._run_ffmpeg(cmd, "add_title")
        except RuntimeError:
            import shutil
            shutil.copy2(input_path, output_path)

    def _wait_for_number(self, telegram: TelegramBot, valid: list[str], timeout_min: int = 15) -> str | None:
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
        cameras = ["Close-up", "Medium wide shot", "Low angle close-up", "Pull-back wide shot"]
        shots = []
        total = len(script.lines)
        for i, line_data in enumerate(script.lines):
            char_desc = get_char_visual(line_data["speaker"])
            cam = cameras[i % len(cameras)]
            if i == total - 1:
                shots.append(f"Shot {i+1}: Slow zoom-in close-up of {char_desc} in {setting}. Character speaks: '{line_data['line']}' then 1 second of silence, holding expression.")
            else:
                shots.append(f"Shot {i+1}: {cam} of {char_desc} in {setting}. Character speaks: '{line_data['line']}'")
        return " ".join(shots) + " No background music. No sound effects. Only character dialogue. Photorealistic, cinematic, warm lighting, 4K."

    def run_daily(self, run_date: str | None = None, dry_run: bool = False, slot: str = "morning") -> dict:
        run_date = run_date or date.today().isoformat()
        output_dir = self.output_base / run_date / slot
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = {"date": run_date, "slot": slot, "status": "started"}

        logger.info(f"{'=' * 50}")
        logger.info(f"LUNA'S LIFE — {run_date} [{slot.upper()}]")
        logger.info(f"{'=' * 50}")

        try:
            seed_gen = SeedGenerator()
            writer = Scriptwriter()
            seedance = SeedanceVideoGenerator()
            uploader = YouTubeUploader()
            telegram = TelegramBot()

            daily = load_daily_story()

            # ==========================
            # SCENE 1: THE INCIDENT (morning)
            # ==========================
            if slot == "morning":
                logger.info("[SCENE 1] The Incident")

                # Check for direct seed
                seed_file = Path(__file__).parent.parent / "data" / "daily_seed.txt"
                file_seed = ""
                if seed_file.exists():
                    file_seed = seed_file.read_text().strip()
                    if file_seed:
                        seed_file.write_text("")

                if file_seed:
                    seeds = seed_gen.generate_seeds(bias=file_seed)
                    chosen_seed = seeds[0]
                else:
                    seeds = seed_gen.generate_seeds()
                    categories = ["💼 WORK", "💕 RELATIONSHIP", "🏠 HOME", "📰 TRENDING", "🤪 WILD CARD"]
                    topic_msg = "🐾 What goes WRONG in Luna's day today?\n\n"
                    for i, s in enumerate(seeds[:5]):
                        cat = categories[i] if i < len(categories) else "🎲"
                        topic_msg += f"{i+1}. {cat} {s.title}\n\"{s.hook}\"\n📍 {s.setting[:40]}\n\n"
                    topic_msg += "6. ✍️ YOUR IDEA\n\n💭 Or share what's on YOUR mind — I'll turn it into Luna's day.\n\nReply 1-6 or type (15 min)."
                    telegram.send_message(topic_msg)

                    if not dry_run:
                        pick = self._wait_for_number(telegram, ["1", "2", "3", "4", "5"], timeout_min=15)
                        chosen_seed = seeds[int(pick) - 1] if pick else seeds[0]
                    else:
                        chosen_seed = seeds[0]

                # Generate 3 scripts → user picks
                options = writer.write_three_options(
                    topic=chosen_seed.premise or chosen_seed.title,
                    character_1=chosen_seed.character,
                    character_2=chosen_seed.character_2 if chosen_seed.character_2 != "none" else "ms_whiskers",
                    setting=chosen_seed.setting,
                    duration=15,
                )
                if not dry_run:
                    telegram.send_message(writer.format_for_telegram(options))
                    pick = self._wait_for_number(telegram, ["1", "2", "3"], timeout_min=15)
                    chosen_script = options[int(pick) - 1] if pick else options[0]
                else:
                    chosen_script = options[0]

                # Save today's story for midday + evening
                save_daily_story({
                    "date": run_date,
                    "theme": chosen_seed.premise or chosen_seed.title,
                    "emotion": chosen_seed.tone,
                    "morning_scene": {
                        "setting": chosen_seed.setting,
                        "characters": [chosen_seed.character, chosen_seed.character_2],
                        "what_happened": chosen_script.lines[0]["line"] if chosen_script.lines else chosen_seed.hook,
                        "script_summary": " ".join(l["line"] for l in chosen_script.lines[:3]),
                    }
                })

            # ==========================
            # SCENE 2: THE VENTING (midday)
            # ==========================
            elif slot == "midday":
                logger.info("[SCENE 2] The Venting")

                if not daily:
                    telegram.send_message("⚠️ No morning scene found. Generating standalone.")
                    seeds = seed_gen.generate_seeds()
                    chosen_seed = seeds[0]
                    chosen_script = writer.write(
                        topic=chosen_seed.premise or chosen_seed.title,
                        character_1=chosen_seed.character,
                        character_2=chosen_seed.character_2 if chosen_seed.character_2 != "none" else "ms_whiskers",
                        setting=chosen_seed.setting,
                        duration=15,
                    )
                else:
                    # Luna vents about what happened this morning
                    morning = daily.get("morning_scene", {})
                    theme = daily.get("theme", "something bad happened")
                    # Different character + setting for venting
                    vent_to = "golden_retriever" if "ms_whiskers" in str(morning.get("characters", [])) else "ms_whiskers"
                    vent_setting = "lunch cafe, outdoor patio, two chairs" if vent_to != "golden_retriever" else "apartment kitchen, coffee mugs on counter"

                    chosen_script = writer.write(
                        topic=f"Luna vents about: {theme}. This morning: {morning.get('script_summary', '')}. Luna tells someone about it, they react.",
                        character_1="orange_cat",
                        character_2=vent_to,
                        setting=vent_setting,
                        duration=15,
                        tone="savage",
                    )

                    # Update daily story
                    daily["midday_scene"] = {
                        "setting": vent_setting,
                        "characters": ["orange_cat", vent_to],
                        "what_happened": chosen_script.lines[-1]["line"] if chosen_script.lines else "Luna vented",
                    }
                    save_daily_story(daily)

            # ==========================
            # SCENE 3: THE AFTERMATH (evening)
            # ==========================
            elif slot == "evening":
                logger.info("[SCENE 3] The Aftermath")

                if not daily:
                    telegram.send_message("⚠️ No earlier scenes found. Generating standalone.")
                    seeds = seed_gen.generate_seeds()
                    chosen_seed = seeds[0]
                    chosen_script = writer.write(
                        topic=chosen_seed.premise or chosen_seed.title,
                        character_1=chosen_seed.character,
                        character_2=chosen_seed.character_2 if chosen_seed.character_2 != "none" else "golden_retriever",
                        setting=chosen_seed.setting,
                        duration=15,
                    )
                else:
                    # Aftermath at home — Luna + Milo + possibly Pickles
                    theme = daily.get("theme", "")
                    morning_summary = daily.get("morning_scene", {}).get("script_summary", "")
                    midday_summary = daily.get("midday_scene", {}).get("what_happened", "")

                    chosen_script = writer.write(
                        topic=f"Aftermath: {theme}. Morning: {morning_summary}. Midday: {midday_summary}. Now Luna is home, exhausted, Milo tries to help.",
                        character_1="orange_cat",
                        character_2="golden_retriever",
                        setting="apartment living room, evening, couch with blankets, dim lamp light",
                        duration=15,
                        tone="wholesome",
                    )

            # ==========================
            # GENERATE + APPROVE + UPLOAD (all slots)
            # ==========================
            setting = chosen_script.visual_notes or "cozy apartment"
            seedance_prompt = self._build_seedance_prompt(chosen_script, setting)

            # Show summary and get approval
            summary_msg = f"🎬 Scene: \"{chosen_script.title}\"\n\n"
            for line in chosen_script.lines:
                summary_msg += f"{line['speaker'].replace('_',' ').title()}: \"{line['line']}\"\n"
            summary_msg += f"\n📍 {setting[:50]}\nReply YES to generate, NO to skip."
            telegram.send_message(summary_msg)

            if not dry_run:
                approved, reason = telegram.wait_for_approval()
                if approved is not True:
                    if reason:
                        save_feedback("prompt_feedback", reason)
                    telegram.send_message("⏭️ Skipped.")
                    summary["status"] = "skipped"
                    return summary

            # Generate video
            logger.info("Generating Seedance video...")
            clip_path = output_dir / "clip.mp4"
            seedance.generate(seedance_prompt, clip_path, duration=15)

            # Add title + episode number
            tracker = load_tracker()
            ep_num = tracker.get("global_episode_count", 0) + 1
            final_path = output_dir / "final.mp4"
            self._add_title(clip_path, final_path, chosen_script.title, ep_num)
            clip_path.unlink(missing_ok=True)

            # Final approval + upload
            title = chosen_script.title
            dialogue = " | ".join(l["line"] for l in chosen_script.lines[:3])
            description = f"{dialogue}...\n\n#shorts #pets #funny #pawsandopinions #lunaslife"
            tags = self.config["seo"]["default_tags"]

            if dry_run:
                logger.info(f"[DRY RUN] {title}")
            else:
                sent = telegram.send_video_for_approval(str(final_path), title, dialogue, 15)
                if sent:
                    approved, reason = telegram.wait_for_approval()
                    if approved is True:
                        uploader.run(long_form_path=final_path, short_paths=[], title=title,
                                     description=description, tags=tags, thumbnail_path=final_path, dry_run=False)
                        telegram.send_completion(title, 15)
                        tracker["global_episode_count"] = ep_num
                        tracker["last_topics"] = (tracker.get("last_topics", []) + [title])[-10:]
                        save_tracker(tracker)
                    elif approved is False:
                        save_feedback("video_feedback", reason)

            summary["status"] = "success"
            summary["title"] = chosen_script.title
            summary["episode"] = ep_num

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            summary["status"] = "failed"
            summary["error"] = str(e)
            try:
                TelegramBot().send_message(f"❌ [{slot}] Failed: {str(e)[:200]}")
            except Exception:
                pass

        logger.info(f"\nPipeline: {summary['status']} [{slot}]")
        return summary
