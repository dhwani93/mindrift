"""MCP Server for Mindrift content database.

Allows conversational interaction with the content tracking database.
Run with: python -m mcp_servers.content_db_server
"""

import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).parent.parent / "data" / "content.db"

mcp = FastMCP("Mindrift — Content DB")


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a query and return results as list of dicts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_publish_history(days: int = 30) -> list[dict]:
    """Get recent publications with titles, dates, and status.

    Args:
        days: Number of days to look back (default 30).
    """
    return _query(
        """SELECT p.title, p.video_type, p.status, p.published_at, p.duration_seconds,
                  p.youtube_video_id, s.category, s.source
           FROM publications p
           LEFT JOIN stories s ON p.story_id = s.id
           WHERE p.created_at >= date('now', ?)
           ORDER BY p.created_at DESC""",
        (f"-{days} days",),
    )


@mcp.tool()
def search_stories(query: str, category: str = "", status: str = "") -> list[dict]:
    """Search stories by keyword, category, or publication status.

    Args:
        query: Search term to match against story titles.
        category: Filter by category (horror, true_crime, unsolved_mystery, etc.).
        status: Filter by publication status (pending, uploaded, failed).
    """
    sql = """SELECT s.title, s.source, s.category, s.created_at,
                    COALESCE(p.status, 'unpublished') as pub_status
             FROM stories s
             LEFT JOIN publications p ON p.story_id = s.id
             WHERE s.title LIKE ?"""
    params = [f"%{query}%"]

    if category:
        sql += " AND s.category = ?"
        params.append(category)
    if status:
        sql += " AND COALESCE(p.status, 'unpublished') = ?"
        params.append(status)

    sql += " ORDER BY s.created_at DESC LIMIT 20"
    return _query(sql, tuple(params))


@mcp.tool()
def check_duplicate(title: str) -> dict:
    """Check if a story with a similar title has been used before.

    Args:
        title: Story title to check for duplicates.
    """
    results = _query(
        "SELECT title, source, category, created_at FROM stories WHERE title LIKE ? LIMIT 5",
        (f"%{title}%",),
    )
    return {"is_duplicate": len(results) > 0, "matches": results}


@mcp.tool()
def get_stats() -> dict:
    """Get overall content statistics: total videos, categories, publishing streak."""
    total_stories = _query("SELECT COUNT(*) as count FROM stories")[0]["count"]
    total_published = _query(
        "SELECT COUNT(*) as count FROM publications WHERE status = 'uploaded'"
    )[0]["count"]
    categories = _query(
        "SELECT category, COUNT(*) as count FROM stories GROUP BY category ORDER BY count DESC"
    )
    recent = _query(
        "SELECT COUNT(*) as count FROM publications WHERE created_at >= date('now', '-7 days')"
    )[0]["count"]

    return {
        "total_stories": total_stories,
        "total_published": total_published,
        "videos_this_week": recent,
        "category_distribution": categories,
    }


@mcp.tool()
def get_monthly_costs() -> list[dict]:
    """Get API cost breakdown for the current month."""
    return _query(
        """SELECT service, SUM(units_used) as total_units,
                  SUM(estimated_cost_usd) as total_cost,
                  COUNT(*) as api_calls
           FROM api_costs
           WHERE date LIKE strftime('%Y-%m', 'now') || '%'
           GROUP BY service"""
    )


if __name__ == "__main__":
    mcp.run()
