"""Preference Learner — accumulates rejection feedback and applies to future generations."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PREFS_PATH = Path(__file__).parent.parent / "data" / "learned_preferences.json"


def load_preferences() -> dict:
    """Load accumulated preferences."""
    if PREFS_PATH.exists():
        return json.loads(PREFS_PATH.read_text())
    return {"script_feedback": [], "prompt_feedback": [], "video_feedback": []}


def save_feedback(category: str, reason: str) -> None:
    """Save a rejection reason to the persistent feedback file.

    Args:
        category: "script_feedback", "prompt_feedback", or "video_feedback"
        reason: Why the user rejected it.
    """
    prefs = load_preferences()
    # Keep last 20 feedback items per category to avoid bloat
    prefs[category].append(reason)
    prefs[category] = prefs[category][-20:]
    PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    logger.info(f"  Saved feedback [{category}]: {reason[:60]}...")


def get_script_rules() -> str:
    """Get accumulated script rules from past rejections."""
    prefs = load_preferences()
    feedback = prefs.get("script_feedback", [])
    if not feedback:
        return ""
    rules = "\n".join(f"- {f}" for f in feedback)
    return f"\nLEARNED FROM PAST REJECTIONS (apply ALL of these):\n{rules}\n"


def get_prompt_rules() -> str:
    """Get accumulated Kling prompt rules from past rejections."""
    prefs = load_preferences()
    feedback = prefs.get("prompt_feedback", [])
    if not feedback:
        return ""
    rules = "\n".join(f"- {f}" for f in feedback)
    return f"\nLEARNED FROM PAST REJECTIONS (apply ALL of these):\n{rules}\n"
