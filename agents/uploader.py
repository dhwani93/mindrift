"""YouTube Uploader Agent — uploads videos via YouTube Data API v3."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


@dataclass
class UploadResult:
    video_id: str
    video_url: str
    status: str


class YouTubeUploader:
    """Uploads videos to YouTube with metadata and scheduling."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.channel_config = self.config["channel"]
        self._youtube = None

    def _get_youtube_client(self):
        """Build an authenticated YouTube API client."""
        if self._youtube:
            return self._youtube

        credentials = Credentials(
            token=None,
            refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["YOUTUBE_CLIENT_ID"],
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        )

        self._youtube = build("youtube", "v3", credentials=credentials)
        return self._youtube

    def _get_publish_time(self, is_short: bool = False, short_index: int = 0) -> str | None:
        """Return None to publish immediately as public."""
        return None

    @retry(max_attempts=5, base_delay=10.0, max_delay=120.0)
    def _upload_video(
        self,
        file_path: Path,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = "22",  # People & Blogs (or "24" for Entertainment)
        is_short: bool = False,
        publish_at: str | None = None,
    ) -> UploadResult:
        """Upload a single video to YouTube.

        Args:
            file_path: Path to the video file.
            title: Video title.
            description: Video description.
            tags: List of tags.
            category_id: YouTube category ID.
            is_short: Whether this is a YouTube Short.
            publish_at: ISO 8601 publish time for scheduled videos.

        Returns:
            UploadResult with video ID and URL.
        """
        youtube = self._get_youtube_client()

        # If scheduling, set as private and use publishAt
        privacy_status = "private" if publish_at else "public"

        body = {
            "snippet": {
                "title": title[:100],  # YouTube limit
                "description": description[:5000],
                "tags": tags[:500],  # YouTube tag limit
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        if publish_at:
            body["status"]["publishAt"] = publish_at

        # Add #Shorts to title if it's a short
        if is_short and "#Shorts" not in title:
            body["snippet"]["title"] = f"{title} #Shorts"

        media = MediaFileUpload(
            str(file_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10MB chunks
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Execute upload with progress logging
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response["id"]
        logger.info(f"Upload complete: https://youtube.com/watch?v={video_id}")

        return UploadResult(
            video_id=video_id,
            video_url=f"https://youtube.com/watch?v={video_id}",
            status="scheduled" if publish_at else "public",
        )

    def upload_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        """Upload a custom thumbnail for a video."""
        youtube = self._get_youtube_client()
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info(f"Thumbnail uploaded for video {video_id}")

    def run(
        self,
        long_form_path: Path,
        short_paths: list[Path],
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Path,
        dry_run: bool = False,
    ) -> list[UploadResult]:
        """Upload long-form video and Shorts to YouTube.

        Args:
            long_form_path: Path to the long-form video.
            short_paths: Paths to Short videos.
            title: Title for the long-form video.
            description: Description for the long-form video.
            tags: Tags for all videos.
            thumbnail_path: Custom thumbnail for long-form.
            dry_run: If True, skip actual upload.

        Returns:
            List of UploadResults.
        """
        results = []

        if dry_run:
            logger.info("[DRY RUN] Skipping YouTube upload")
            logger.info(f"  Long-form: {long_form_path} | Title: {title}")
            for i, sp in enumerate(short_paths):
                logger.info(f"  Short {i + 1}: {sp}")
            return results

        # Upload long-form
        publish_time = self._get_publish_time(is_short=False)
        logger.info(f"Uploading long-form: '{title}' (scheduled for {publish_time})")

        result = self._upload_video(
            file_path=long_form_path,
            title=title,
            description=description,
            tags=tags,
            publish_at=publish_time,
        )
        results.append(result)

        # Upload thumbnail
        try:
            self.upload_thumbnail(result.video_id, thumbnail_path)
        except Exception as e:
            logger.warning(f"Thumbnail upload failed (may need channel verification): {e}")

        # Upload Shorts
        for i, short_path in enumerate(short_paths):
            short_publish = self._get_publish_time(is_short=True, short_index=i)
            short_title = f"{title} - Part {i + 1}"

            logger.info(f"Uploading Short {i + 1}: '{short_title}'")
            short_result = self._upload_video(
                file_path=short_path,
                title=short_title,
                description=f"{description}\n\n#Shorts #Horror #Mystery",
                tags=tags,
                is_short=True,
                publish_at=short_publish,
            )
            results.append(short_result)

        logger.info(f"All uploads complete: {len(results)} videos")
        return results
