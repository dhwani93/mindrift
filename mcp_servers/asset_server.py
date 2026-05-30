"""MCP Server for Paws & Opinions asset library.

Browse and search local music, SFX, and cached images.
Run with: python -m mcp_servers.asset_server
"""

import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ASSETS_DIR = Path(__file__).parent.parent / "assets"
DB_PATH = Path(__file__).parent.parent / "data" / "content.db"

mcp = FastMCP("Paws & Opinions — Asset Library")


@mcp.tool()
def list_music(category: str = "") -> list[dict]:
    """List available music tracks, optionally filtered by category.

    Args:
        category: Filter by category: 'drones', 'tension', or 'stingers'. Leave empty for all.
    """
    music_dir = ASSETS_DIR / "music"
    results = []

    if category:
        search_dirs = [music_dir / category]
    else:
        search_dirs = [d for d in music_dir.iterdir() if d.is_dir()]

    for dir_path in search_dirs:
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob("*")):
            if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac"):
                results.append({
                    "name": f.name,
                    "category": dir_path.name,
                    "path": str(f.relative_to(ASSETS_DIR)),
                    "size_kb": f.stat().st_size // 1024,
                })

    return results


@mcp.tool()
def list_sfx(query: str = "") -> list[dict]:
    """List available sound effects, optionally filtered by name.

    Args:
        query: Search term to filter SFX files by name.
    """
    sfx_dir = ASSETS_DIR / "sfx"
    results = []

    if not sfx_dir.exists():
        return results

    for f in sorted(sfx_dir.rglob("*")):
        if f.suffix.lower() in (".mp3", ".wav", ".ogg"):
            if not query or query.lower() in f.stem.lower():
                results.append({
                    "name": f.stem,
                    "path": str(f.relative_to(ASSETS_DIR)),
                    "size_kb": f.stat().st_size // 1024,
                })

    return results


@mcp.tool()
def get_asset_usage(asset_name: str) -> dict:
    """Check how many times a specific asset has been used in videos.

    Args:
        asset_name: Name or partial path of the asset to check.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT asset_path, asset_type, COUNT(*) as times_used,
                  MAX(used_at) as last_used
           FROM asset_usage
           WHERE asset_path LIKE ?
           GROUP BY asset_path""",
        (f"%{asset_name}%",),
    ).fetchall()
    conn.close()

    return {
        "matches": [dict(r) for r in rows],
        "total_uses": sum(r["times_used"] for r in rows),
    }


@mcp.tool()
def get_library_summary() -> dict:
    """Get a summary of the entire asset library: counts by type and category."""
    summary = {"music": {}, "sfx": 0, "fonts": 0, "overlays": 0}

    # Music by category
    music_dir = ASSETS_DIR / "music"
    if music_dir.exists():
        for d in music_dir.iterdir():
            if d.is_dir():
                count = len(list(d.glob("*.*")))
                summary["music"][d.name] = count

    # SFX
    sfx_dir = ASSETS_DIR / "sfx"
    if sfx_dir.exists():
        summary["sfx"] = len(list(sfx_dir.rglob("*.mp3")) + list(sfx_dir.rglob("*.wav")))

    # Fonts
    fonts_dir = ASSETS_DIR / "fonts"
    if fonts_dir.exists():
        summary["fonts"] = len(list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf")))

    # Overlays
    overlays_dir = ASSETS_DIR / "overlays"
    if overlays_dir.exists():
        summary["overlays"] = len(list(overlays_dir.glob("*.png")))

    return summary


if __name__ == "__main__":
    mcp.run()
