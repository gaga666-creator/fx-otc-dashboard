from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Quote, SOURCE_META, empty_quote


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "rates.db"


def db_path() -> Path:
    raw = os.getenv("DATABASE_URL", "")
    if raw.startswith("sqlite:///"):
        path = raw.removeprefix("sqlite:///")
        return (BASE_DIR.parent / path).resolve() if path.startswith("./") else Path(path)
    return DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS latest_quotes (
                source_key TEXT PRIMARY KEY,
                value REAL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                last_success_time TEXT,
                debug_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                value REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refreshed_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )


def get_latest() -> dict[str, Quote]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM latest_quotes").fetchall()
    quotes = {key: empty_quote(key) for key in SOURCE_META}
    for row in rows:
        quotes[row["source_key"]] = Quote(
            key=row["source_key"],
            value=row["value"],
            status=row["status"],
            source=row["source"],
            source_url=row["source_url"],
            last_success_time=row["last_success_time"],
            debug=json.loads(row["debug_json"] or "{}"),
        )
    return quotes


def save_quotes(quotes: Iterable[Quote], refreshed_at: str) -> None:
    with connect() as conn:
        for quote in quotes:
            conn.execute(
                """
                INSERT INTO latest_quotes
                    (source_key, value, status, source, source_url, last_success_time, debug_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    value=excluded.value,
                    status=excluded.status,
                    source=excluded.source,
                    source_url=excluded.source_url,
                    last_success_time=excluded.last_success_time,
                    debug_json=excluded.debug_json,
                    updated_at=excluded.updated_at
                """,
                (
                    quote.key,
                    quote.value,
                    quote.status,
                    quote.source,
                    quote.source_url,
                    quote.last_success_time,
                    json.dumps(quote.debug, ensure_ascii=False),
                    refreshed_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO quote_snapshots (source_key, value, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (quote.key, quote.value, quote.status, refreshed_at),
            )


def save_refresh_log(refreshed_at: str, result: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO refresh_log (refreshed_at, result_json) VALUES (?, ?)",
            (refreshed_at, json.dumps(result, ensure_ascii=False)),
        )


def last_refresh_time() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT refreshed_at FROM refresh_log ORDER BY id DESC LIMIT 1").fetchone()
    return row["refreshed_at"] if row else None


def snapshots(hours: int = 168) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT source_key, value, status, created_at
            FROM quote_snapshots
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at ASC
            """,
            (f"-{hours} hours",),
        ).fetchall()
    return [dict(row) for row in rows]

