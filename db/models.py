"""
SQLite schema and initialization for 1C Error Analyzer.
"""
import os
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    base_name TEXT NOT NULL,
    archive_name TEXT NOT NULL,
    config_type TEXT,
    error_description TEXT,
    stack_json TEXT,
    error_code_line TEXT,
    application TEXT,
    window_title TEXT,
    window_type TEXT,
    screen_summary TEXT,
    confidence REAL,
    input_fields_json TEXT,
    tables_json TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    last_resolution_comment TEXT
);

CREATE TABLE IF NOT EXISTS error_solutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    comment TEXT,
    FOREIGN KEY (error_id) REFERENCES errors(id)
);

CREATE INDEX IF NOT EXISTS idx_errors_status ON errors(status);
CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at);
CREATE INDEX IF NOT EXISTS idx_errors_base_name ON errors(base_name);
CREATE INDEX IF NOT EXISTS idx_error_solutions_error_id ON error_solutions(error_id);
"""

VALID_STATUSES = frozenset({"new", "in_progress", "resolved", "ignored"})


def get_db_path() -> Path:
    """Return path to SQLite database file. Uses DB_PATH from env if set, else project root / errors.db."""
    env_path = os.environ.get("DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent / "errors.db"


def init_db(db_path: Path | None = None) -> None:
    """Create database and tables if they do not exist."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        logger.debug("Database initialized at %s", path)
    finally:
        conn.close()
