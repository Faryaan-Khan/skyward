"""SQLite persistence for fare history, watches and alerts."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("SKYWARD_DB", os.path.join(os.path.dirname(__file__), "..", "skyward.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS fares (
    id INTEGER PRIMARY KEY,
    route TEXT NOT NULL,
    observed DATE NOT NULL,
    depart DATE NOT NULL,
    price REAL NOT NULL,
    is_sale INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'synthetic'
);
CREATE INDEX IF NOT EXISTS ix_fares_route ON fares(route, depart);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY,
    route TEXT NOT NULL,
    depart DATE NOT NULL,
    target_price REAL,
    email TEXT,
    telegram_chat_id TEXT,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    watch_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(seed_if_empty: bool = True) -> None:
    from .synthetic import generate

    with connect() as c:
        c.executescript(SCHEMA)
        if seed_if_empty and c.execute("SELECT COUNT(*) FROM fares").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO fares(route, observed, depart, price, is_sale) VALUES (?,?,?,?,?)",
                ((r, o.isoformat(), d.isoformat(), p, int(s)) for r, o, d, p, s in generate()),
            )
