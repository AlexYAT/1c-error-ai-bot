"""
Repository layer for errors and error_solutions.
"""
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from db.models import get_db_path, init_db, VALID_STATUSES

logger = logging.getLogger(__name__)


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    init_db(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_error(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("stack_json", "input_fields_json", "tables_json"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                pass
    return d


def insert_error(
    base_name: str,
    archive_name: str,
    config_type: str | None,
    error_description: str | None,
    stack_json: str | None,
    error_code_line: str | None,
    application: str | None,
    window_title: str | None,
    window_type: str | None,
    screen_summary: str | None,
    confidence: float | None,
    input_fields_json: str | None,
    tables_json: str | None,
    db_path: Path | None = None,
) -> int:
    """Insert new error; returns new id."""
    now = datetime.utcnow().isoformat() + "Z"
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO errors (
                created_at, updated_at, base_name, archive_name,
                config_type, error_description, stack_json, error_code_line,
                application, window_title, window_type, screen_summary,
                confidence, input_fields_json, tables_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                now,
                now,
                base_name,
                archive_name,
                config_type,
                error_description,
                stack_json,
                error_code_line,
                application,
                window_title,
                window_type,
                screen_summary,
                confidence,
                input_fields_json,
                tables_json,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_errors(
    status: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List errors, optionally filtered by status."""
    conn = _connect(db_path)
    try:
        if status:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}. Use one of: {VALID_STATUSES}")
            cur = conn.execute(
                "SELECT * FROM errors WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cur = conn.execute("SELECT * FROM errors ORDER BY created_at DESC")
        return [_row_to_error(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_error_by_id(error_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Get single error by id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM errors WHERE id = ?", (error_id,))
        row = cur.fetchone()
        return _row_to_error(row) if row else None
    finally:
        conn.close()


def set_status(
    error_id: int,
    new_status: str,
    comment: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Update error status and optionally add resolution comment. Returns True if updated."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}. Use one of: {VALID_STATUSES}")
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT id, status FROM errors WHERE id = ?", (error_id,))
        row = cur.fetchone()
        if not row:
            return False
        old_status = row["status"]
        now = datetime.utcnow().isoformat() + "Z"
        conn.execute(
            "UPDATE errors SET updated_at = ?, status = ?, last_resolution_comment = ? WHERE id = ?",
            (now, new_status, comment or None, error_id),
        )
        conn.execute(
            "INSERT INTO error_solutions (error_id, created_at, old_status, new_status, comment) VALUES (?, ?, ?, ?, ?)",
            (error_id, now, old_status, new_status, comment),
        )
        conn.commit()
        logger.info("Error %s: %s -> %s", error_id, old_status, new_status)
        return True
    finally:
        conn.close()


def find_similar(
    error_description: str | None,
    module_name: str | None,
    code_line: str | None,
    exclude_id: int | None = None,
    limit: int = 3,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Find top N similar errors (legacy: simple LIKE match). Prefer similarity.find_similar_errors for scored results."""
    conn = _connect(db_path)
    try:
        conditions = []
        params: list[Any] = []
        if error_description:
            conditions.append("error_description LIKE ?")
            params.append(f"%{error_description[:100]}%")
        if module_name:
            conditions.append("(stack_json LIKE ? OR error_code_line LIKE ?)")
            params.extend([f"%{module_name}%", f"%{module_name}%"])
        if code_line:
            conditions.append("error_code_line LIKE ?")
            params.append(f"%{code_line[:200]}%")
        if exclude_id is not None:
            conditions.append("id != ?")
            params.append(exclude_id)
        if not conditions:
            return []
        where = " AND ".join(conditions)
        params.append(limit)
        cur = conn.execute(
            f"SELECT * FROM errors WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return [_row_to_error(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_errors_for_similarity(
    exclude_id: int | None = None,
    max_rows: int = 200,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch error rows for similarity (id, base_name, error_description, stack_json, error_code_line, window_type, window_title, status, created_at)."""
    conn = _connect(db_path)
    try:
        if exclude_id is not None:
            cur = conn.execute(
                """SELECT id, base_name, error_description, stack_json, error_code_line, window_type, window_title, status, created_at
                   FROM errors WHERE id != ? ORDER BY created_at DESC LIMIT ?""",
                (exclude_id, max_rows),
            )
        else:
            cur = conn.execute(
                """SELECT id, base_name, error_description, stack_json, error_code_line, window_type, window_title, status, created_at
                   FROM errors ORDER BY created_at DESC LIMIT ?""",
                (max_rows,),
            )
        return [_row_to_error(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_solution_for_error(error_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return latest solution (comment, created_at) for error from error_solutions or errors, or None."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """SELECT comment, created_at FROM error_solutions WHERE error_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (error_id,),
        )
        row = cur.fetchone()
        if row:
            return {"comment": row["comment"], "created_at": row["created_at"]}
        cur2 = conn.execute(
            "SELECT last_resolution_comment, updated_at FROM errors WHERE id = ? AND status = 'resolved'",
            (error_id,),
        )
        r2 = cur2.fetchone()
        if r2 and (r2["last_resolution_comment"] or r2["updated_at"]):
            return {"comment": r2["last_resolution_comment"], "created_at": r2["updated_at"]}
        return None
    finally:
        conn.close()


def report_by_date(
    date_from: str,
    date_to: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Aggregate report: count by status, by base_name, list recent."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT status, COUNT(*) as cnt FROM errors
            WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
            GROUP BY status
            """,
            (date_from, date_to),
        )
        by_status = {row["status"]: row["cnt"] for row in cur.fetchall()}
        cur = conn.execute(
            """
            SELECT base_name, COUNT(*) as cnt FROM errors
            WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
            GROUP BY base_name
            """,
            (date_from, date_to),
        )
        by_base = {row["base_name"]: row["cnt"] for row in cur.fetchall()}
        cur = conn.execute(
            """
            SELECT * FROM errors
            WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
            ORDER BY created_at DESC
            """,
            (date_from, date_to),
        )
        recent = [_row_to_error(r) for r in cur.fetchall()]
        return {
            "from": date_from,
            "to": date_to,
            "by_status": by_status,
            "by_base": by_base,
            "total": sum(by_status.values()),
            "recent": recent,
        }
    finally:
        conn.close()
