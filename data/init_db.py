"""Initialize the SQLite database schema for Mindrift."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "content.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    category TEXT NOT NULL,
    raw_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER REFERENCES stories(id),
    video_type TEXT NOT NULL,
    youtube_video_id TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    published_at TIMESTAMP,
    status TEXT DEFAULT 'pending',
    duration_seconds INTEGER,
    file_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_path TEXT NOT NULL,
    publication_id INTEGER REFERENCES publications(id),
    asset_type TEXT NOT NULL,
    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    service TEXT NOT NULL,
    units_used REAL,
    estimated_cost_usd REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stories_body_hash ON stories(body_hash);
CREATE INDEX IF NOT EXISTS idx_stories_source ON stories(source);
CREATE INDEX IF NOT EXISTS idx_publications_status ON publications(status);
CREATE INDEX IF NOT EXISTS idx_publications_story_id ON publications(story_id);
CREATE INDEX IF NOT EXISTS idx_api_costs_date ON api_costs(date);
"""


def init_database(db_path: Path = DB_PATH) -> None:
    """Create database and tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database initialized at {db_path}")


if __name__ == "__main__":
    init_database()
