"""SQLite storage for prices, announcements and a fetch log."""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,          -- YYYY-MM-DD (exchange date)
    open      REAL, high REAL, low REAL, close REAL, adj_close REAL,
    volume    INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS announcements (
    id          TEXT PRIMARY KEY,     -- "bse:<NEWSID>" / "nse:<seq_id>"
    symbol      TEXT NOT NULL,
    dt          TEXT NOT NULL,        -- ISO datetime, IST
    headline    TEXT,
    category    TEXT,
    subcategory TEXT,
    pdf_url     TEXT,
    source      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ann_symbol_dt ON announcements(symbol, dt);
CREATE TABLE IF NOT EXISTS fetch_log (
    source     TEXT NOT NULL,
    key        TEXT NOT NULL,
    fetched_at REAL NOT NULL,         -- unix seconds
    PRIMARY KEY (source, key)
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()
