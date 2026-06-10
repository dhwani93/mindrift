"""Telegram bot — sends seed options, parses replies, handles video approval."""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for daily seeds and video approval."""

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, text: str) -> bool:
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def send_seeds(self, formatted_seeds: str) -> bool:
        """Send the formatted seed options to user."""
        return self.send_message(formatted_seeds)

    def get_latest_message(self, max_age_hours: int = 3) -> str | None:
        """Get the latest message from user within max_age_hours."""
        try:
            r = requests.get(f"{self.base_url}/getUpdates", timeout=10)
            data = r.json()

            if not data.get("result"):
                return None

            for update in reversed(data["result"]):
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                    msg_time = msg.get("date", 0)
                    age_hours = (time.time() - msg_time) / 3600
                    if age_hours <= max_age_hours:
                        text = msg.get("text", "")
                        skip_words = ("hi", "hello", "hey", "yes", "y", "no", "n", "skip", "nope", "post", "go", "approve", "reject")
                        if text and not text.startswith("/") and text.lower().strip() not in skip_words:
                            return text
            return None
        except Exception as e:
            logger.error(f"Telegram read failed: {e}")
            return None

    def check_for_command(self, max_age_hours: int = 3) -> dict | None:
        """Check if user sent a /command. Returns parsed command or None."""
        try:
            r = requests.get(f"{self.base_url}/getUpdates", timeout=10)
            data = r.json()
            if not data.get("result"):
                return None
            for update in reversed(data["result"]):
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                    msg_time = msg.get("date", 0)
                    age_hours = (time.time() - msg_time) / 3600
                    if age_hours <= max_age_hours:
                        text = msg.get("text", "").strip()
                        if text.startswith("/advance"):
                            return {"command": "advance"}
                        elif text.startswith("/addtopic "):
                            return {"command": "addtopic", "value": text[10:].strip()}
                        elif text.startswith("/addera "):
                            return {"command": "addera", "value": text[8:].strip()}
                        elif text.startswith("/status"):
                            return {"command": "status"}
            return None
        except Exception:
            return None

    def parse_seed_reply(self, reply: str) -> dict:
        """Parse user's reply to seed options.

        Returns dict with:
            - seed_number: int or None (1-5)
            - modifier: str or None ("more savage", "make it 60 sec", etc.)
            - custom_idea: str or None (if user typed their own idea)
        """
        reply = reply.strip()
        result = {"seed_number": None, "modifier": None, "custom_idea": None}

        # Check if starts with a number 1-5
        if reply and reply[0].isdigit():
            num = int(reply[0])
            if 1 <= num <= 5:
                result["seed_number"] = num
                rest = reply[1:].strip().lstrip(".-,").strip()
                if rest:
                    result["modifier"] = rest
                return result

        # Check for modifier keywords without a number (applies to top seed)
        modifier_keywords = ["more savage", "more wholesome", "more absurd", "more finance",
                            "more couple", "more drama", "make it 60", "make it longer",
                            "make it darker", "make it funnier"]
        reply_lower = reply.lower()
        for kw in modifier_keywords:
            if kw in reply_lower:
                result["seed_number"] = 1  # Apply to top seed
                result["modifier"] = reply
                return result

        # Otherwise treat as custom idea
        result["custom_idea"] = reply
        return result

    def send_video_for_approval(self, video_path: str, title: str, script: str, duration: float) -> bool:
        """Send generated video to user for approval."""
        try:
            with open(video_path, "rb") as video_file:
                r = requests.post(
                    f"{self.base_url}/sendVideo",
                    data={
                        "chat_id": self.chat_id,
                        "caption": (
                            f"🎬 {title} ({duration:.0f}s)\n\n"
                            f'"{script[:200]}"\n\n'
                            f"Reply YES to post, NO to skip."
                        ),
                    },
                    files={"video": video_file},
                    timeout=60,
                )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram video send failed: {e}")
            return False

    def wait_for_approval(self, timeout_minutes: int = 0) -> tuple[bool, str] | tuple[None, str]:
        """Wait for YES/NO approval. Returns (approved, feedback_reason).

        If user says YES → (True, "")
        If user says NO → asks why, waits for reason → (False, "reason text")
        """
        # Clear old updates
        requests.get(f"{self.base_url}/getUpdates", params={"offset": -1}, timeout=10)
        time.sleep(1)
        r = requests.get(f"{self.base_url}/getUpdates", timeout=10)
        if r.json().get("result"):
            last_id = r.json()["result"][-1]["update_id"]
            requests.get(f"{self.base_url}/getUpdates", params={"offset": last_id + 1}, timeout=10)

        start = time.time()
        max_wait = timeout_minutes * 60 if timeout_minutes > 0 else float('inf')
        while time.time() - start < max_wait:
            try:
                r = requests.get(f"{self.base_url}/getUpdates", params={"timeout": 30}, timeout=40)
                for update in r.json().get("result", []):
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                        text = msg.get("text", "").strip().lower()
                        requests.get(f"{self.base_url}/getUpdates", params={"offset": update["update_id"] + 1}, timeout=10)
                        if text in ("yes", "y", "post", "go", "approve", "👍"):
                            self.send_message("✅ Got it!")
                            return (True, "")
                        elif text in ("no", "n", "skip", "nope", "reject", "👎"):
                            self.send_message("❌ Got it. Tell me WHY so I learn and don't repeat this mistake:")
                            # Wait for the reason
                            reason = self._wait_for_text(timeout_minutes=30)
                            if reason:
                                self.send_message(f"📝 Learned: \"{reason[:100]}\". Regenerating...")
                            return (False, reason or "no reason given")
            except Exception as e:
                logger.debug(f"Polling error: {e}")
            time.sleep(5)

        # Should never reach here with infinite wait, but just in case
        return (None, "")

    def _wait_for_text(self, timeout_minutes: int = 30) -> str:
        """Wait for any text message from user (for rejection reasons)."""
        import time
        start = time.time()
        while time.time() - start < timeout_minutes * 60:
            try:
                r = requests.get(f"{self.base_url}/getUpdates", params={"timeout": 30}, timeout=40)
                for update in r.json().get("result", []):
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                        text = msg.get("text", "").strip()
                        requests.get(f"{self.base_url}/getUpdates", params={"offset": update["update_id"] + 1}, timeout=10)
                        if text and not text.startswith("/"):
                            return text
            except Exception:
                pass
            time.sleep(5)
        return ""

    def send_completion(self, title: str, duration: float) -> bool:
        return self.send_message(f"🎉 Posted! \"{title}\" ({duration:.0f}s)")

    def send_reminder(self) -> bool:
        """Legacy reminder — now replaced by send_seeds in the pipeline."""
        return self.send_message(
            "🐾 Generating today's episode seeds...\n"
            "You'll get 5 options in a moment. Sit tight!"
        )
