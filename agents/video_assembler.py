"""Video Assembler Agent — creates vertical short-form videos from stock footage + narration."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from agents.image_curator import CuratedMedia, MediaAsset
from agents.script_writer import Script, ScriptPart
from agents.voice_generator import VoiceOutput

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


@dataclass
class PartVideo:
    part_number: int
    video_path: Path
    duration_seconds: float


@dataclass
class VideoOutput:
    part_videos: list[PartVideo]


class VideoAssembler:
    """Assembles vertical short-form videos from stock footage, narration, and overlays."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.video_config = self.config["video"]
        self.fps = self.video_config["fps"]
        # Vertical 9:16
        self.width = 1080
        self.height = 1920
        self.zoom_speed = self.video_config["ken_burns_zoom_speed"]

    def _run_ffmpeg(self, cmd: list[str], description: str = "") -> None:
        """Run an FFmpeg command and handle errors."""
        logger.debug(f"FFmpeg ({description}): {' '.join(cmd[:10])}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"FFmpeg error ({description}): {result.stderr[-300:]}")
            raise RuntimeError(f"FFmpeg failed ({description}): {result.stderr[-200:]}")

    def _make_image_clip(self, image_path: Path, duration: float, output_path: Path) -> None:
        """Create a Ken Burns clip from a static image (vertical)."""
        frames = int(duration * self.fps)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(image_path),
            "-vf", (
                f"scale=-1:{self.height * 2},"
                f"zoompan=z='min(zoom+{self.zoom_speed},1.4)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={self.width}x{self.height}:fps={self.fps}"
            ),
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, f"image_clip_{output_path.stem}")

    def _make_video_clip(self, video_path: Path, duration: float, output_path: Path) -> None:
        """Process a stock video clip: crop to vertical, trim to duration."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-t", str(duration),
            "-vf", (
                f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height},"
                "setsar=1"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",  # Remove original audio
            str(output_path),
        ]
        self._run_ffmpeg(cmd, f"video_clip_{output_path.stem}")

    def _concat_clips_with_crossfade(self, clip_paths: list[Path], output_path: Path, crossfade_duration: float = 0.3) -> None:
        """Concatenate clips with crossfade transitions using xfade filter."""
        if len(clip_paths) < 2:
            if clip_paths:
                import shutil
                shutil.copy2(clip_paths[0], output_path)
            return

        # For crossfade, we need to chain xfade filters
        # Build complex filter: [0][1]xfade=transition=fade:duration=0.3:offset=X[v01];[v01][2]xfade=...
        # Get durations of each clip
        durations = []
        for clip in clip_paths:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(clip)],
                capture_output=True, text=True,
            )
            try:
                durations.append(float(result.stdout.strip()))
            except ValueError:
                durations.append(5.0)  # fallback

        # Build inputs
        inputs = []
        for clip in clip_paths:
            inputs.extend(["-i", str(clip)])

        # Build xfade filter chain
        cf = crossfade_duration
        filter_parts = []
        current_offset = durations[0] - cf

        if len(clip_paths) == 2:
            filter_parts.append(
                f"[0:v][1:v]xfade=transition=fade:duration={cf}:offset={current_offset}[outv]"
            )
        else:
            # First pair
            filter_parts.append(
                f"[0:v][1:v]xfade=transition=fade:duration={cf}:offset={current_offset}[v01]"
            )
            # Middle pairs
            for i in range(2, len(clip_paths)):
                current_offset += durations[i - 1] - cf
                prev_label = f"v{i-2:02d}{i-1:02d}" if i > 2 else "v01"
                if i == len(clip_paths) - 1:
                    filter_parts.append(
                        f"[{prev_label}][{i}:v]xfade=transition=fade:duration={cf}:offset={current_offset}[outv]"
                    )
                else:
                    next_label = f"v{i-1:02d}{i:02d}"
                    filter_parts.append(
                        f"[{prev_label}][{i}:v]xfade=transition=fade:duration={cf}:offset={current_offset}[{next_label}]"
                    )

        filter_str = ";".join(filter_parts)

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_str,
            "-map", "[outv]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]

        try:
            self._run_ffmpeg(cmd, "crossfade_concat")
        except RuntimeError:
            # Fallback: simple concat without crossfade
            logger.warning("Crossfade failed, using simple concat")
            concat_file = output_path.parent / f"concat_{output_path.stem}.txt"
            with open(concat_file, "w") as f:
                for clip in clip_paths:
                    f.write(f"file '{clip}'\n")
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_file), "-c", "copy", str(output_path),
            ]
            self._run_ffmpeg(cmd, "simple_concat")
            concat_file.unlink()

    def _apply_color_grade(self, input_path: Path, output_path: Path) -> None:
        """Apply dark moody color grade + vignette."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", (
                "vignette=PI/4,"
                "eq=brightness=-0.06:saturation=0.8,"
                "curves=m='0/0 0.25/0.18 0.5/0.45 0.75/0.78 1/1'"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, "color_grade")

    def _merge_audio(self, video_path: Path, audio_path: Path, output_path: Path, duration: float = 0) -> None:
        """Merge narration audio with video, trimming to exact audio duration."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
        ]
        # Explicitly trim to audio duration to avoid dead air
        if duration > 0:
            cmd.extend(["-t", str(duration)])
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ])
        self._run_ffmpeg(cmd, "merge_audio")

    def _add_hook_text(self, input_path: Path, output_path: Path, text: str) -> None:
        """Add hook text overlay in the first 3 seconds."""
        # Escape text for FFmpeg drawtext
        safe_text = text.replace("'", "\u2019").replace(":", " -").replace("\\", "")
        # Split long text into 2 lines
        if len(safe_text) > 25:
            mid = len(safe_text) // 2
            space_idx = safe_text.rfind(" ", 0, mid + 5)
            if space_idx > 0:
                safe_text = safe_text[:space_idx] + "\\n" + safe_text[space_idx + 1:]

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", (
                f"drawtext=text='{safe_text}'"
                ":fontsize=48:fontcolor=white:borderw=3:bordercolor=black"
                f":x=(w-text_w)/2:y=h*0.40"
                ":enable='between(t,0.5,3.5)'"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, "hook_text")

    def assemble_part(
        self,
        part: ScriptPart,
        media_assets: list[MediaAsset],
        audio_path: Path,
        audio_duration: float,
        output_dir: Path,
    ) -> PartVideo:
        """Assemble a single part video.

        Args:
            part: Script part with narration and prompts.
            media_assets: Downloaded media for this part.
            audio_path: Path to narration audio for this part.
            audio_duration: Duration of the audio in seconds.
            output_dir: Where to save output.

        Returns:
            PartVideo with path and duration.
        """
        temp_dir = output_dir / f"temp_part_{part.part_number}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Create clips from each media asset
        clips = []
        total_target_duration = audio_duration  # Match video to audio length

        for i, asset in enumerate(media_assets):
            clip_path = temp_dir / f"clip_{i:02d}.mp4"
            # Scale duration proportionally to fill the audio
            duration = asset.duration_sec

            try:
                if asset.media_type == "video":
                    self._make_video_clip(asset.path, duration, clip_path)
                else:
                    self._make_image_clip(asset.path, duration, clip_path)
                clips.append(clip_path)
            except Exception as e:
                logger.warning(f"Part {part.part_number}, clip {i} failed: {e}")

        if not clips:
            raise RuntimeError(f"Part {part.part_number}: no clips could be created")

        # Step 2: Concatenate all clips with crossfade transitions
        concat_path = temp_dir / "concat.mp4"
        self._concat_clips_with_crossfade(clips, concat_path)

        # Step 3: Color grade
        graded_path = temp_dir / "graded.mp4"
        self._apply_color_grade(concat_path, graded_path)

        # Step 4: Merge narration audio (trim video to exact audio length)
        with_audio_path = temp_dir / "with_audio.mp4"
        self._merge_audio(graded_path, audio_path, with_audio_path, duration=audio_duration)

        # Step 5: Add hook text overlay (skip if drawtext filter unavailable)
        final_path = output_dir / f"part_{part.part_number:02d}.mp4"
        if part.hook_text:
            try:
                self._add_hook_text(with_audio_path, final_path, part.hook_text)
            except RuntimeError as e:
                logger.warning(f"Hook text overlay failed (missing drawtext filter), skipping: {e}")
                import shutil
                shutil.copy2(with_audio_path, final_path)
        else:
            import shutil
            shutil.copy2(with_audio_path, final_path)

        # Cleanup temp
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info(f"Part {part.part_number} assembled: {final_path}")

        return PartVideo(
            part_number=part.part_number,
            video_path=final_path,
            duration_seconds=audio_duration,
        )

    def run(
        self,
        script: Script,
        media: CuratedMedia,
        voice_outputs: list[VoiceOutput],
        output_dir: Path,
    ) -> VideoOutput:
        """Assemble all parts into final short-form videos.

        Args:
            script: Multi-part script.
            media: Curated media organized by part number.
            voice_outputs: Voice output for each part.
            output_dir: Output directory.

        Returns:
            VideoOutput with list of part videos.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        part_videos = []

        for i, part in enumerate(script.parts):
            logger.info(f"Assembling Part {part.part_number}...")
            assets = media.get_assets_for_part(part.part_number)
            voice = voice_outputs[i]

            try:
                part_video = self.assemble_part(
                    part=part,
                    media_assets=assets,
                    audio_path=voice.processed_audio_path,
                    audio_duration=voice.duration_seconds,
                    output_dir=output_dir,
                )
                part_videos.append(part_video)
            except Exception as e:
                logger.error(f"Part {part.part_number} assembly failed: {e}")

        logger.info(f"Video assembly complete: {len(part_videos)} parts")
        return VideoOutput(part_videos=part_videos)
