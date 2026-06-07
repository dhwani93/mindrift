"""Kling Lip Sync — adds TTS + lip sync to an existing Kling video."""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
import requests

from utils.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class LipSyncResult:
    video_path: Path
    duration_seconds: float


class KlingLipSync:
    """Adds text-to-speech with lip sync to a Kling-generated video."""

    def __init__(self):
        self.access_key = os.environ.get("KLING_API_KEY", "")
        self.secret_key = os.environ.get("KLING_API_SECRET", "")
        self.base_url = "https://api.klingai.com"

    def _get_token(self) -> str:
        headers = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.access_key,
            "exp": int(time.time()) + 1800,
            "nbf": int(time.time()) - 5,
        }
        return jwt.encode(payload, self.secret_key, algorithm="HS256", headers=headers)

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    @retry(max_attempts=3, base_delay=10.0)
    def _submit_lipsync(self, video_task_id: str, text: str, voice: str = "en_male_1", speed: float = 1.0) -> str:
        """Submit a lip sync task using Kling's TTS mode.

        Args:
            video_task_id: Task ID from a previous text2video generation.
            text: The dialogue text for TTS.
            voice: Voice timbre ID.
            speed: Speech speed multiplier.

        Returns:
            Task ID for polling.
        """
        payload = {
            "input": {
                "video_id": video_task_id,
                "mode": "text2video",
                "text": text,
                "voice_id": voice,
                "voice_speed": speed,
            }
        }

        response = requests.post(
            f"{self.base_url}/v1/videos/lip-sync",
            headers=self._get_headers(),
            json=payload,
            timeout=30,
        )
        if response.status_code != 200:
            logger.error(f"Kling lip sync error {response.status_code}: {response.text[:300]}")
            response.raise_for_status()

        data = response.json()
        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in lip sync response: {data}")

        logger.info(f"Lip sync task submitted: {task_id}")
        return task_id

    def _poll_task(self, task_id: str, max_wait: int = 600) -> str:
        """Poll for lip sync completion."""
        start = time.time()
        while time.time() - start < max_wait:
            response = requests.get(
                f"{self.base_url}/v1/videos/lip-sync/{task_id}",
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
                    return videos[0].get("url", "")
                raise RuntimeError(f"Lip sync succeeded but no video URL: {task_data}")
            elif status == "failed":
                reason = task_data.get("task_status_msg", "unknown")
                raise RuntimeError(f"Lip sync failed: {reason}")

            time.sleep(10)

        raise TimeoutError(f"Lip sync task {task_id} timed out")

    def _download_video(self, url: str, output_path: Path) -> None:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        logger.info(f"Downloaded lip-synced video: {output_path} ({len(response.content) // 1024}KB)")

    def run(self, video_task_id: str, text: str, output_path: Path, voice: str = "en_male_1") -> LipSyncResult:
        """Generate lip-synced version of a Kling video.

        Args:
            video_task_id: Task ID from text2video generation.
            text: Dialogue text.
            output_path: Where to save the result.
            voice: Voice timbre.

        Returns:
            LipSyncResult with path and duration.
        """
        logger.info(f"Lip syncing: \"{text[:50]}...\"")
        task_id = self._submit_lipsync(video_task_id, text, voice)
        video_url = self._poll_task(task_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_video(video_url, output_path)
        return LipSyncResult(video_path=output_path, duration_seconds=5)
