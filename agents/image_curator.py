"""Media Curator Agent — fetches stock videos and images from Pexels for each video beat."""

import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from agents.script_writer import Script, ScriptPart
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


@dataclass
class MediaAsset:
    path: Path
    media_type: str  # "video" or "image"
    duration_sec: float
    description: str


@dataclass
class CuratedMedia:
    """Media assets organized by part number."""
    parts: dict[int, list[MediaAsset]] = field(default_factory=dict)

    def get_assets_for_part(self, part_number: int) -> list[MediaAsset]:
        return self.parts.get(part_number, [])


class MediaCurator:
    """Fetches stock videos and images from Pexels based on script video prompts."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.api_key = os.environ.get("PEXELS_API_KEY", "")
        self.base_url = "https://api.pexels.com"
        self.use_stock_video = self.config["video"].get("use_stock_video", True)

    @retry(max_attempts=3, base_delay=2.0)
    def _search_videos(self, query: str, per_page: int = 5) -> list[dict]:
        """Search Pexels for stock videos."""
        headers = {"Authorization": self.api_key}
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": "portrait",  # Vertical for Shorts/Reels
            "size": "medium",
        }
        response = requests.get(f"{self.base_url}/videos/search", headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("videos", [])

    @retry(max_attempts=3, base_delay=2.0)
    def _search_images(self, query: str, per_page: int = 5) -> list[dict]:
        """Search Pexels for images (fallback if no video found)."""
        headers = {"Authorization": self.api_key}
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": "portrait",
            "size": "large",
        }
        response = requests.get(f"{self.base_url}/v1/search", headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("photos", [])

    def _download_video(self, video_data: dict, output_path: Path) -> Path:
        """Download a video from Pexels. Picks the best quality file that fits."""
        video_files = video_data.get("video_files", [])
        # Prefer HD portrait/vertical files
        suitable = [
            f for f in video_files
            if f.get("height", 0) >= 720 and f.get("width", 0) <= f.get("height", 0)
        ]
        if not suitable:
            # Fallback: any HD file
            suitable = [f for f in video_files if f.get("height", 0) >= 720]
        if not suitable:
            suitable = video_files

        if not suitable:
            raise ValueError("No suitable video file found")

        # Pick the best quality
        chosen = sorted(suitable, key=lambda f: f.get("height", 0), reverse=True)[0]
        url = chosen["link"]

        response = requests.get(url, timeout=60)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        return output_path

    def _download_image(self, photo: dict, output_path: Path) -> Path:
        """Download an image from Pexels."""
        url = photo["src"].get("large2x", photo["src"]["large"])
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        return output_path

    def run(self, script: Script, output_dir: Path) -> CuratedMedia:
        """Fetch media assets for each part's video prompts.

        Args:
            script: Multi-part script with video_prompts per part.
            output_dir: Directory to save downloaded media.

        Returns:
            CuratedMedia with assets organized by part number.
        """
        media_dir = output_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        curated = CuratedMedia()

        for part in script.parts:
            part_assets = []
            part_dir = media_dir / f"part_{part.part_number:02d}"
            part_dir.mkdir(exist_ok=True)

            for i, beat in enumerate(part.video_prompts):
                query = beat.description
                logger.info(f"Part {part.part_number}, beat {i}: '{query}'")

                asset = None

                # Try stock video first
                if self.use_stock_video:
                    try:
                        videos = self._search_videos(query)
                        if videos:
                            video_data = random.choice(videos[:3])
                            video_path = part_dir / f"beat_{i:02d}.mp4"
                            self._download_video(video_data, video_path)
                            asset = MediaAsset(
                                path=video_path,
                                media_type="video",
                                duration_sec=beat.duration_sec,
                                description=query,
                            )
                            logger.info(f"  → Downloaded stock video (id: {video_data['id']})")
                    except Exception as e:
                        logger.warning(f"  → Video fetch failed: {e}, trying image...")

                # Fallback to image
                if not asset:
                    try:
                        photos = self._search_images(f"{query} dark eerie")
                        if not photos:
                            photos = self._search_images(query)
                        if photos:
                            photo = random.choice(photos[:3])
                            img_path = part_dir / f"beat_{i:02d}.jpg"
                            self._download_image(photo, img_path)
                            asset = MediaAsset(
                                path=img_path,
                                media_type="image",
                                duration_sec=beat.duration_sec,
                                description=query,
                            )
                            logger.info(f"  → Downloaded image (id: {photo['id']})")
                    except Exception as e:
                        logger.error(f"  → Image fetch also failed: {e}")

                if asset:
                    part_assets.append(asset)

            curated.parts[part.part_number] = part_assets
            logger.info(f"Part {part.part_number}: {len(part_assets)} media assets")

        return curated
