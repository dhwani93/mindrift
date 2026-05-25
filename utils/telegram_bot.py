"""Telegram bot utility — sends reminders and reads daily seeds."""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


class TelegramBot:
    """Simple Telegram bot for daily thought seeds."""

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, text: str) -> bool:
        """Send a message to the user."""
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def get_latest_message(self, max_age_hours: int = 24) -> str | None:
        """Get the latest message from the user within max_age_hours.

        Returns:
            The message text, or None if no recent message.
        """
        try:
            r = requests.get(f"{self.base_url}/getUpdates", timeout=10)
            data = r.json()

            if not data.get("result"):
                return None

            # Get the most recent message from the user
            latest = None
            for update in reversed(data["result"]):
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                    # Check age
                    msg_time = msg.get("date", 0)
                    age_hours = (time.time() - msg_time) / 3600
                    if age_hours <= max_age_hours:
                        text = msg.get("text", "")
                        # Ignore bot commands and greetings
                        if text and not text.startswith("/") and text.lower() not in ("hi", "hello", "hey"):
                            latest = text
                            break

            return latest

        except Exception as e:
            logger.error(f"Telegram read failed: {e}")
            return None

    def send_reminder(self) -> bool:
        """Send the daily thought reminder."""
        text = (
            "🌀 What's today's thought?\n\n"
            "Drop a mind-bending idea and I'll turn it into a video.\n\n"
            "Examples:\n"
            "• What if Germany won WW2\n"
            "• Hidden city under the Himalayas\n"
            "• What if dinosaurs built a civilization\n\n"
            "Add 'long' for a 30s video, otherwise I'll keep it ~15s.\n\n"
            "Skip this and I'll auto-generate one."
        )
        return self.send_message(text)

    def send_video_for_approval(self, video_path: str, thought: str, duration: float) -> bool:
        """Send the generated video to user for approval."""
        try:
            # Send the video file
            with open(video_path, "rb") as video_file:
                r = requests.post(
                    f"{self.base_url}/sendVideo",
                    data={
                        "chat_id": self.chat_id,
                        "caption": (
                            f"🎬 Today's video ({duration:.0f}s)\n\n"
                            f'"{thought}"\n\n'
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

    def wait_for_approval(self, timeout_minutes: int = 30) -> bool | None:
        """Wait for user to approve or reject the video.

        Returns:
            True if approved, False if rejected, None if timeout.
        """
        import time as _time

        # Clear old updates first
        requests.get(f"{self.base_url}/getUpdates", params={"offset": -1}, timeout=10)
        _time.sleep(1)
        # Mark current updates as read
        r = requests.get(f"{self.base_url}/getUpdates", timeout=10)
        if r.json().get("result"):
            last_id = r.json()["result"][-1]["update_id"]
            requests.get(f"{self.base_url}/getUpdates", params={"offset": last_id + 1}, timeout=10)

        start = _time.time()
        while _time.time() - start < timeout_minutes * 60:
            try:
                r = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={"timeout": 30},
                    timeout=40,
                )
                data = r.json()

                for update in data.get("result", []):
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                        text = msg.get("text", "").strip().lower()
                        # Mark as read
                        requests.get(
                            f"{self.base_url}/getUpdates",
                            params={"offset": update["update_id"] + 1},
                            timeout=10,
                        )
                        if text in ("yes", "y", "post", "go", "👍", "approve"):
                            self.send_message("✅ Posting now!")
                            return True
                        elif text in ("no", "n", "skip", "nope", "👎", "reject"):
                            self.send_message("⏭️ Skipped. Will try again tomorrow.")
                            return False

            except Exception as e:
                logger.debug(f"Polling error: {e}")

            _time.sleep(5)

        self.send_message("⏰ No response — skipping today's upload.")
        return None

    def send_completion(self, title: str, duration: float) -> bool:
        """Notify user that video was posted."""
        text = f"🎉 Posted! Title: {title} ({duration:.0f}s)"
        return self.send_message(text)
