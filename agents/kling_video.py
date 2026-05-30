"""Kling AI Video Generator — generates short AI videos using JWT auth."""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
import requests

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class KlingVideo:
    video_path: Path
    duration_seconds: float


class KlingVideoGenerator:
    """Generates AI videos using Kling API (text-to-video) with JWT authentication."""

    def __init__(self):
        self.access_key = os.environ.get("KLING_API_KEY", "")
        self.secret_key = os.environ.get("KLING_API_SECRET", "")
        self.base_url = "https://api.klingai.com"

    def _get_token(self) -> str:
        """Generate JWT token from access key and secret key."""
        headers = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.access_key,
            "exp": int(time.time()) + 1800,  # 30 min expiry
            "nbf": int(time.time()) - 5,
        }
        token = jwt.encode(payload, self.secret_key, algorithm="HS256", headers=headers)
        return token

    def _get_headers(self) -> dict:
        """Get request headers with fresh JWT token."""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    @retry(max_attempts=3, base_delay=10.0)
    def _submit_generation(self, prompt: str, duration: int = 5) -> str:
        """Submit a text-to-video generation task.

        Args:
            prompt: Visual description for the video.
            duration: Video duration in seconds (5 or 10).

        Returns:
            Task ID for polling.
        """
        # Sanitize prompt: remove special chars, cap length
        clean_prompt = prompt.replace("[", "").replace("]", "").replace("{", "").replace("}", "")
        clean_prompt = clean_prompt.replace("\n", " ").replace("\r", " ")
        clean_prompt = " ".join(clean_prompt.split())  # normalize whitespace
        if len(clean_prompt) > 2000:
            clean_prompt = clean_prompt[:2000].rsplit(" ", 1)[0]

        payload = {
            "model_name": "kling-v1",
            "prompt": clean_prompt,
            "negative_prompt": "text, words, subtitles, captions, speech bubbles, dialogue, letters, numbers, watermark, logo, realistic, photorealistic, scary, horror, dark, ugly, distorted face, extra limbs, extra fingers, blurry, low quality, deformed, mutated, disfigured",
            "duration": str(duration),
            "aspect_ratio": "9:16",
            "mode": "pro",
            "cfg_scale": 0.7,
        }

        response = requests.post(
            f"{self.base_url}/v1/videos/text2video",
            headers=self._get_headers(),
            json=payload,
            timeout=30,
        )
        if response.status_code != 200:
            logger.error(f"Kling API error {response.status_code}: {response.text[:500]}")
            response.raise_for_status()
        data = response.json()

        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {data}")

        logger.info(f"Kling task submitted: {task_id}")
        return task_id

    def _poll_task(self, task_id: str, max_wait: int = 600) -> str:
        """Poll for task completion and return video URL.

        Args:
            task_id: The generation task ID.
            max_wait: Maximum seconds to wait.

        Returns:
            URL of the generated video.
        """
        start = time.time()
        while time.time() - start < max_wait:
            response = requests.get(
                f"{self.base_url}/v1/videos/text2video/{task_id}",
                headers=self._get_headers(),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            task_data = data.get("data", {})
            status = task_data.get("task_status", "")

            if status == "succeed":
                videos = task_data.get("task_result", {}).get("videos", [])
                if videos:
                    url = videos[0].get("url", "")
                    logger.info(f"Kling video ready: {url[:80]}...")
                    return url
                raise RuntimeError(f"Task succeeded but no video URL: {task_data}")

            elif status == "failed":
                reason = task_data.get("task_status_msg", "unknown")
                raise RuntimeError(f"Kling generation failed: {reason}")

            logger.debug(f"Kling task {task_id}: status={status}, waiting...")
            time.sleep(15)

        raise TimeoutError(f"Kling task {task_id} timed out after {max_wait}s")

    def _download_video(self, url: str, output_path: Path) -> None:
        """Download generated video to local file."""
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        logger.info(f"Downloaded Kling video: {output_path} ({len(response.content) // 1024}KB)")

    def generate(self, prompt: str, output_path: Path, duration: int = 5) -> KlingVideo:
        """Generate a video from a text prompt.

        Args:
            prompt: Detailed visual description.
            output_path: Where to save the video.
            duration: Video duration (5 or 10 seconds).

        Returns:
            KlingVideo with path and duration.
        """
        logger.info(f"Generating Kling video ({duration}s): {prompt[:80]}...")

        task_id = self._submit_generation(prompt, duration)
        video_url = self._poll_task(task_id)

        if not video_url:
            raise RuntimeError("No video URL returned from Kling")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_video(video_url, output_path)

        # Log cost estimate (~$0.075/sec)
        cost = duration * 0.075
        log_cost("kling", duration, cost)

        return KlingVideo(video_path=output_path, duration_seconds=duration)
