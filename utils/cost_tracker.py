"""Track API costs per daily run."""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "content.db"


def log_cost(service: str, units_used: float, estimated_cost_usd: float, db_path: Path = DB_PATH) -> None:
    """Log an API cost entry."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO api_costs (date, service, units_used, estimated_cost_usd) VALUES (?, ?, ?, ?)",
        (date.today().isoformat(), service, units_used, estimated_cost_usd),
    )
    conn.commit()
    conn.close()


def get_monthly_costs(db_path: Path = DB_PATH) -> dict[str, float]:
    """Get total costs per service for the current month."""
    conn = sqlite3.connect(db_path)
    month_prefix = date.today().strftime("%Y-%m")
    rows = conn.execute(
        "SELECT service, SUM(estimated_cost_usd) FROM api_costs WHERE date LIKE ? GROUP BY service",
        (f"{month_prefix}%",),
    ).fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}
