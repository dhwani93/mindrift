"""Kling Prompt Builder — uses Claude to generate ultra-detailed video prompts."""

import json
import logging
from pathlib import Path

import anthropic
import yaml

from utils.cost_tracker import log_cost
from utils.retry import retry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

SYSTEM_PROMPT = """You are a world-class cinematographer and production designer writing video generation prompts for Kling AI.

Your job: take a rough scene description and expand it into an ULTRA-DETAILED prompt that Kling AI can render into a stunning 10-second video clip. Kling renders EXACTLY what you describe — every detail you add makes the video better. Every detail you omit is left to chance.

You must write like you're briefing a VFX team building a shot for a $200M film. Leave NOTHING to interpretation.

YOUR PROMPT MUST COVER ALL OF THESE IN EXTREME DETAIL:

## 1. CAMERA (be specific about movement, speed, angle, lens)
- Type: steadicam, aerial drone, crane, dolly, handheld, locked tripod, orbiting
- Movement: direction, speed (slow crawl, walking pace, sweeping), path (linear, arc, descending)
- Angle: low angle looking up, eye level, bird's eye, dutch angle, over-the-shoulder
- Lens feel: wide angle (shows scale), telephoto (compression, intimacy), macro (extreme detail)

## 2. ENVIRONMENT (build the world in exhaustive detail)
For cities/civilizations:
- Architecture: what style? (Gothic, Art Deco, Brutalist, organic, crystalline, Aztec-inspired) What materials? (obsidian, white marble, living wood, translucent crystal, rusted iron, black granite) How tall? How old? What condition? (pristine, crumbling, overgrown with vines, partially submerged)
- Streets/pathways: what are they made of? (polished stone, glowing tiles, suspended glass bridges, cobblestone, gold-inlaid pathways) How wide? What's on them?
- Vegetation: specific plants (bioluminescent mushrooms, hanging vines with blue flowers, enormous ferns, moss-covered everything, alien coral-like growths)
- Water features: waterfalls, rivers, reflecting pools, fountains, underground lakes with crystal-clear water showing the bottom
- Scale references: tiny figures walking, birds flying, vehicles/vessels for scale comparison

For landscapes/nature:
- Geology: rock types, formations, colors (red sandstone cliffs, black volcanic glass, white chalk, blue ice)
- Weather: specific cloud types, precipitation, wind effects on vegetation/dust/water
- Flora: species-level detail where possible, or very specific descriptions of fictional plants

For interiors:
- Walls: material, texture, decorations (carved reliefs depicting what? murals showing what? mounted artifacts?)
- Floor: material, pattern, reflections, debris
- Ceiling: height, structure (vaulted, domed, open to sky, stalactites), what's hanging from it
- Objects: furniture, artifacts, technology — each described with material, size, condition

## 3. MOTION (multiple layers of movement create life)
Layer 1 - Camera movement (described above)
Layer 2 - Primary motion: the main moving element (a figure walking, a door opening, water flowing)
Layer 3 - Secondary motion: environmental movement (leaves falling, dust swirling, light shifting, clouds drifting)
Layer 4 - Subtle motion: tiny details that add life (flickering flames, dripping water, insects, cloth rippling, reflections shimmering)

## 4. LIGHTING (this makes or breaks the shot)
- Primary light source: what is it? (sun at what angle, bioluminescent organisms, artificial lamps, fire, glowing crystals, moonlight) What color? What intensity?
- Secondary light: fill light, ambient glow, reflections off surfaces
- Shadows: where do they fall? How sharp? What shapes do they create?
- Atmospheric effects: volumetric fog/mist (how thick? what color?), god rays, light shafts, haze, underwater caustics
- Color palette: dominant colors and accent colors (e.g., "cold blue-gray stone with warm amber torchlight accents")

## 5. TEXTURE & SURFACE DETAIL
- Weathering: rust, patina, erosion, moss growth, water stains, cracks, peeling paint
- Reflections: wet surfaces, polished metal, still water, glass
- Particles: dust motes in light beams, embers, pollen, snow, ash, floating spores
- Material properties: translucent, matte, glossy, rough-hewn, smooth, crystalline

## 6. QUALITY TAGS (always end with these)
"cinematic photorealistic 4K HDR, shallow depth of field, anamorphic lens flare, film grain, color graded"

RESPOND WITH JSON:
{
  "scenes": [
    {
      "scene_number": 1,
      "description": "The full ultra-detailed prompt (aim for 150-250 words per scene — more detail = better video)"
    },
    {
      "scene_number": 2,
      "description": "Second scene prompt, equally detailed"
    }
  ]
}

Generate exactly 2 scenes. Each scene will be a 10-second Kling video clip.
Scene 1 = the approach/establishing shot.
Scene 2 = the reveal/payoff shot.

DO NOT hold back on detail. The more specific you are, the better the video. There is no such thing as too much detail for Kling."""


class KlingPromptBuilder:
    """Generates ultra-detailed Kling video prompts using Claude."""

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = yaml.safe_load(f)
        self.client = anthropic.Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    @retry(max_attempts=3, base_delay=2.0)
    def build_prompts(self, thought_text: str, rough_scenes: list[str]) -> list[str]:
        """Take rough scene descriptions and expand into ultra-detailed Kling prompts.

        Args:
            thought_text: The voiceover text (for context).
            rough_scenes: Rough scene descriptions from the thought generator.

        Returns:
            List of ultra-detailed Kling prompts.
        """
        scenes_text = "\n".join(f"- Scene {i+1}: {s}" for i, s in enumerate(rough_scenes))

        user_prompt = f"""Expand these rough scene descriptions into ultra-detailed Kling AI video prompts.

VOICEOVER CONTEXT: "{thought_text}"

ROUGH SCENES:
{scenes_text}

MANDATORY — EVERY scene MUST start with a camera direction line in this exact format:
"[CAMERA TYPE] at [HEIGHT/ANGLE], moving [DIRECTION] at [SPEED], [LENS]mm lens."

Examples:
"Steadicam at eye level (5.5ft), pushing forward at slow walking pace, 35mm wide lens."
"Aerial drone at 200ft descending at 45-degree angle, sweeping left-to-right, 24mm ultra-wide lens."
"Crane shot starting at ground level, rising vertically to 80ft, orbiting 90 degrees clockwise, 50mm lens."
"Locked tripod at low angle (2ft), static with slow 10-degree upward tilt over 10 seconds, 85mm telephoto lens."

After the camera line, describe EVERYTHING else: environment, materials, colors (with RGB), textures, motion layers (primary + secondary + ambient), lighting sources, atmospheric effects, particles, scale references.

200-400 words per scene minimum. JSON only."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        cost = (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000
        log_cost("claude_kling_prompt", response.usage.input_tokens + response.usage.output_tokens, cost)

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(text)
        detailed_prompts = [scene["description"] for scene in data["scenes"]]

        for i, prompt in enumerate(detailed_prompts):
            logger.info(f"  Kling prompt {i+1}: {len(prompt)} chars, {len(prompt.split())} words")

        return detailed_prompts
