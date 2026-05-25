"""SEO Optimizer Agent — generates titles, descriptions, tags, and thumbnails."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic
import yaml
from PIL import Image, ImageDraw, ImageFont

from agents.script_writer import Script
from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SEO_SYSTEM_PROMPT = """You are a YouTube SEO expert specializing in horror/mystery/true crime content.
Generate metadata that maximizes click-through rate while accurately representing the content.

TITLE RULES:
- Maximum 60 characters
- Use patterns that create curiosity gaps:
  - "The [Adjective] Case of [Name/Place]"
  - "Something Was Wrong With [Subject]"
  - "I Should Have Never [Action]"
  - "They Found [Object] in [Place]"
  - "The [Place] That [Verb] Back"
  - "Nobody Talks About What Happened at [Place]"
- Do NOT use all-caps or excessive punctuation
- Do NOT use "You Won't Believe" or similar clickbait

DESCRIPTION RULES:
- First 2 lines (150 chars) are the hook — shown in search results
- Include timestamps (fabricate reasonable ones for an 8-12 min video)
- Include relevant hashtags at the end
- Credit the source if applicable
- End with: "Subscribe to Mindrift for daily horror narration."

TAGS RULES:
- 15-20 tags
- Mix broad ("horror story") and specific (story-related terms)
- Include "the hollow hour" as a tag
- Include variations people might search

Respond with JSON:
{
  "title": "...",
  "description": "...",
  "tags": ["tag1", "tag2", ...],
  "thumbnail_text": "2-4 word text for the thumbnail (short, punchy, all caps)"
}"""


@dataclass
class SEOOutput:
    title: str
    description: str
    tags: list[str]
    thumbnail_path: Path


class SEOOptimizer:
    """Generates YouTube SEO metadata and thumbnails."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.default_tags = self.config["seo"]["default_tags"]

    @retry(max_attempts=2, base_delay=2.0)
    def _generate_metadata(self, script: Script) -> dict:
        """Use Claude to generate SEO-optimized metadata."""
        part1 = script.parts[0] if script.parts else None
        hook = part1.hook_text if part1 else ""
        narration_preview = part1.narration[:300] if part1 else ""

        user_prompt = f"""Generate YouTube Shorts metadata for this horror narration series:

STORY TITLE: {script.story_title}
HOOK: {hook}
NARRATION PREVIEW: {narration_preview}...
FORMAT: {script.num_parts}-part series, each 60-90 seconds (YouTube Shorts / Reels / TikTok)

Generate a title, description, tags, and thumbnail text. The title should work for Shorts (short + punchy)."""

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SEO_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Log cost
        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_seo", response.usage.input_tokens + response.usage.output_tokens, cost)

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    def _generate_thumbnail(
        self, image_path: Path, text: str, output_path: Path
    ) -> Path:
        """Generate a thumbnail: darken image + add bold text."""
        img = Image.open(image_path).convert("RGB")
        img = img.resize((1280, 720), Image.LANCZOS)

        # Darken the image
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(0.5)  # 50% darker

        # Add slight color tint (blue/teal)
        overlay = Image.new("RGB", img.size, (0, 30, 50))
        img = Image.blend(img, overlay, 0.2)

        # Add text
        draw = ImageDraw.Draw(img)

        # Try to load a bold font, fall back to default
        font_size = 80
        try:
            font_dir = Path(__file__).parent.parent / "assets" / "fonts"
            font_path = font_dir / "Montserrat-Bold.ttf"
            if font_path.exists():
                font = ImageFont.truetype(str(font_path), font_size)
            else:
                font = ImageFont.load_default()
                font_size = 40
        except Exception:
            font = ImageFont.load_default()
            font_size = 40

        # Center the text
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (1280 - text_width) // 2
        y = (720 - text_height) // 2

        # Draw text with outline
        outline_color = (0, 0, 0)
        for dx in [-3, -2, 0, 2, 3]:
            for dy in [-3, -2, 0, 2, 3]:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=(255, 255, 255))

        img.save(output_path, quality=95)
        logger.info(f"Thumbnail generated: {output_path}")
        return output_path

    def run(self, script: Script, cover_image_path: Path, output_dir: Path, part_number: int = 1) -> SEOOutput:
        """Generate SEO metadata and thumbnail.

        Args:
            script: The narration script.
            cover_image_path: A striking image to use for the thumbnail.
            part_number: Which part this is for (affects title/thumbnail).
            output_dir: Where to save the thumbnail.

        Returns:
            SEOOutput with all metadata and thumbnail path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate metadata via Claude
        metadata = self._generate_metadata(script)

        # Merge default tags with generated ones
        all_tags = list(set(self.default_tags + metadata.get("tags", [])))

        # Generate thumbnail
        thumbnail_path = output_dir / f"thumbnail_part_{part_number}.png"
        thumbnail_text = metadata.get("thumbnail_text", script.story_title[:20].upper())
        if part_number > 1:
            thumbnail_text = f"PART {part_number}"
        self._generate_thumbnail(cover_image_path, thumbnail_text, thumbnail_path)

        logger.info(f"SEO complete: title='{metadata['title']}'")

        return SEOOutput(
            title=metadata["title"],
            description=metadata["description"],
            tags=all_tags,
            thumbnail_path=thumbnail_path,
        )
