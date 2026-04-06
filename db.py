"""
SQLite persistence layer for LLM Credit Hunter.
Tracks model history (first_seen, last_seen, disappeared) and signals.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "credits.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            name TEXT,
            provider TEXT,
            context_length INTEGER,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            is_available INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT,
            url TEXT,
            snippets TEXT,
            points INTEGER DEFAULT 0,
            first_seen TEXT NOT NULL,
            scan_date TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            model_count INTEGER DEFAULT 0,
            signal_count INTEGER DEFAULT 0,
            new_models INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        );

        CREATE INDEX IF NOT EXISTS idx_models_available ON models(is_available);
        CREATE INDEX IF NOT EXISTS idx_models_first_seen ON models(first_seen);
        CREATE INDEX IF NOT EXISTS idx_signals_scan_date ON signals(scan_date);
    """)
    conn.close()


def upsert_models(models: list[dict]) -> dict:
    """Insert/update models. Returns counts of new, updated, disappeared."""
    conn = get_db()
    now = datetime.now().isoformat()
    seen_ids = set()
    new_count = 0

    for m in models:
        mid = m["id"]
        seen_ids.add(mid)
        existing = conn.execute("SELECT id, is_available FROM models WHERE id = ?", (mid,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE models SET last_seen = ?, is_available = 1, context_length = ?, name = ? WHERE id = ?",
                (now, m.get("context_length", 0), m.get("name", ""), mid)
            )
        else:
            conn.execute(
                "INSERT INTO models (id, name, provider, context_length, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                (mid, m.get("name", ""), m.get("provider", ""), m.get("context_length", 0), now, now)
            )
            new_count += 1

    # Mark models not seen in this scan as unavailable
    if seen_ids:
        placeholders = ",".join("?" * len(seen_ids))
        disappeared = conn.execute(
            f"UPDATE models SET is_available = 0 WHERE id NOT IN ({placeholders}) AND is_available = 1",
            list(seen_ids)
        ).rowcount
    else:
        disappeared = 0

    conn.commit()
    conn.close()
    return {"new": new_count, "updated": len(models) - new_count, "disappeared": disappeared}


def upsert_signals(signals: list[dict]) -> int:
    """Insert new signals (deduped by url). Returns count of new signals."""
    conn = get_db()
    now = datetime.now().isoformat()
    new_count = 0

    for s in signals:
        url = s.get("url", "")
        if not url:
            continue
        existing = conn.execute("SELECT id FROM signals WHERE url = ?", (url,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO signals (source, title, url, snippets, points, first_seen, scan_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (s.get("source", ""), s.get("title", ""), url,
                 str(s.get("snippets", "")), s.get("points", 0), now, now)
            )
            new_count += 1

    conn.commit()
    conn.close()
    return new_count


def record_scan(started_at: str, model_count: int, signal_count: int, new_models: int, new_signals: int) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO scans (started_at, finished_at, model_count, signal_count, new_models, new_signals, status) VALUES (?, ?, ?, ?, ?, ?, 'complete')",
        (started_at, datetime.now().isoformat(), model_count, signal_count, new_models, new_signals)
    )
    scan_id = cur.lastrowid
    conn.commit()
    conn.close()
    return scan_id


def get_models(available_only: bool = True) -> list[dict]:
    conn = get_db()
    if available_only:
        rows = conn.execute("SELECT * FROM models WHERE is_available = 1 ORDER BY context_length DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM models ORDER BY is_available DESC, context_length DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signals(limit: int = 100) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM signals ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_scans(limit: int = 10) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_db()
    models_available = conn.execute("SELECT COUNT(*) FROM models WHERE is_available = 1").fetchone()[0]
    models_total = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    signals_total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    scans_total = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    last_scan = conn.execute("SELECT finished_at FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return {
        "models_available": models_available,
        "models_total": models_total,
        "signals_total": signals_total,
        "scans_total": scans_total,
        "last_scan": last_scan[0] if last_scan else None,
    }


# Auto-init on import
init_db()
