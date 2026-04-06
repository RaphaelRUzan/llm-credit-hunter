"""
SQLite persistence layer for LLM Credit Hunter.
Tracks model history, benchmarks, capabilities, and user preferences.
"""

import json
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

        CREATE TABLE IF NOT EXISTS capabilities (
            model_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (model_id, capability),
            FOREIGN KEY (model_id) REFERENCES models(id)
        );

        CREATE TABLE IF NOT EXISTS benchmarks (
            model_id TEXT NOT NULL,
            benchmark TEXT NOT NULL,
            score REAL,
            source TEXT,
            category TEXT,
            updated_at TEXT,
            PRIMARY KEY (model_id, benchmark),
            FOREIGN KEY (model_id) REFERENCES models(id)
        );

        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            model_id TEXT,
            action TEXT NOT NULL DEFAULT 'boost',
            weight REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            CHECK (action IN ('pin', 'block', 'boost'))
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
        CREATE INDEX IF NOT EXISTS idx_benchmarks_model ON benchmarks(model_id);
        CREATE INDEX IF NOT EXISTS idx_benchmarks_category ON benchmarks(category);
        CREATE INDEX IF NOT EXISTS idx_preferences_task ON preferences(task_type);
    """)
    conn.close()


# ── Models ──────────────────────────────────────────────────────────────────

def upsert_models(models: list[dict]) -> dict:
    conn = get_db()
    now = datetime.now().isoformat()
    seen_ids = set()
    new_count = 0

    for m in models:
        mid = m["id"]
        seen_ids.add(mid)
        existing = conn.execute("SELECT id FROM models WHERE id = ?", (mid,)).fetchone()
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

        # Upsert capabilities
        caps = {
            "modality": m.get("modality", "text->text"),
            "input_modalities": json.dumps(m.get("input_modalities", ["text"])),
            "output_modalities": json.dumps(m.get("output_modalities", ["text"])),
            "supports_tools": str(m.get("supports_tools", False)),
            "supports_reasoning": str(m.get("supports_reasoning", False)),
            "supports_structured": str(m.get("supports_structured", False)),
            "max_completion": str(m.get("max_completion", 0)),
            "description": m.get("description", "")[:500],
        }
        for cap, val in caps.items():
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO capabilities (model_id, capability, value) VALUES (?, ?, ?)",
                    (mid, cap, val)
                )

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


# ── Benchmarks ──────────────────────────────────────────────────────────────

def upsert_benchmarks(benchmarks: list[dict]) -> int:
    conn = get_db()
    now = datetime.now().isoformat()
    count = 0
    for b in benchmarks:
        conn.execute(
            "INSERT OR REPLACE INTO benchmarks (model_id, benchmark, score, source, category, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (b["model_id"], b["benchmark"], b["score"], b.get("source", ""), b.get("category", ""), now)
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_benchmarks(model_id: str = None) -> list[dict]:
    conn = get_db()
    if model_id:
        rows = conn.execute("SELECT * FROM benchmarks WHERE model_id = ? ORDER BY benchmark", (model_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT b.* FROM benchmarks b JOIN models m ON b.model_id = m.id WHERE m.is_available = 1 ORDER BY b.model_id, b.benchmark"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_models_with_benchmarks(available_only: bool = True) -> list[dict]:
    """Get models with their benchmark scores pivoted into columns."""
    conn = get_db()
    where = "WHERE m.is_available = 1" if available_only else ""
    models = conn.execute(f"""
        SELECT m.*, GROUP_CONCAT(b.benchmark || '::' || b.score || '::' || b.category, '||') as bench_data
        FROM models m
        LEFT JOIN benchmarks b ON m.id = b.model_id
        {where}
        GROUP BY m.id
        ORDER BY m.context_length DESC
    """).fetchall()
    conn.close()

    result = []
    for m in models:
        d = dict(m)
        d["benchmarks"] = {}
        if d.get("bench_data"):
            for entry in d["bench_data"].split("||"):
                parts = entry.split("::")
                if len(parts) == 3:
                    d["benchmarks"][parts[0]] = {"score": float(parts[1]), "category": parts[2]}
        del d["bench_data"]
        result.append(d)
    return result


# ── Capabilities ────────────────────────────────────────────────────────────

def get_capabilities(model_id: str) -> dict:
    conn = get_db()
    rows = conn.execute("SELECT capability, value FROM capabilities WHERE model_id = ?", (model_id,)).fetchall()
    conn.close()
    return {r["capability"]: r["value"] for r in rows}


def get_all_capabilities() -> dict[str, dict]:
    """Returns {model_id: {capability: value}} for all available models."""
    conn = get_db()
    rows = conn.execute("""
        SELECT c.model_id, c.capability, c.value
        FROM capabilities c JOIN models m ON c.model_id = m.id
        WHERE m.is_available = 1
    """).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["model_id"], {})[r["capability"]] = r["value"]
    return result


# ── Preferences ─────────────────────────────────────────────────────────────

def set_preference(task_type: str, model_id: str, action: str, weight: float = 1.0) -> int:
    conn = get_db()
    # Remove existing preference for this task+model combo
    conn.execute("DELETE FROM preferences WHERE task_type = ? AND model_id = ?", (task_type, model_id))
    cur = conn.execute(
        "INSERT INTO preferences (task_type, model_id, action, weight, created_at) VALUES (?, ?, ?, ?, ?)",
        (task_type, model_id, action, weight, datetime.now().isoformat())
    )
    conn.commit()
    pref_id = cur.lastrowid
    conn.close()
    return pref_id


def delete_preference(pref_id: int):
    conn = get_db()
    conn.execute("DELETE FROM preferences WHERE id = ?", (pref_id,))
    conn.commit()
    conn.close()


def get_preferences(task_type: str = None) -> list[dict]:
    conn = get_db()
    if task_type:
        rows = conn.execute("SELECT * FROM preferences WHERE task_type = ? ORDER BY action, weight DESC", (task_type,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM preferences ORDER BY task_type, action, weight DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Recommend Engine ────────────────────────────────────────────────────────

TASK_BENCHMARK_WEIGHTS = {
    "coding": {"SWE-bench Verified": 0.3, "Arena Coding": 0.25, "IFEval": 0.15, "BBH": 0.15, "HumanEval": 0.15},
    "math": {"Arena Math": 0.3, "MATH Lvl 5": 0.3, "MATH": 0.2, "GPQA": 0.1, "BBH": 0.1},
    "reasoning": {"Arena Hard Prompts": 0.3, "BBH": 0.25, "MUSR": 0.25, "GPQA": 0.2},
    "science": {"GPQA": 0.4, "MMLU-PRO": 0.3, "Arena Elo": 0.2, "BBH": 0.1},
    "knowledge": {"MMLU-PRO": 0.3, "MMLU": 0.2, "Arena Elo": 0.2, "GPQA": 0.15, "BBH": 0.15},
    "instruction_following": {"Arena IF": 0.3, "IFEval": 0.4, "Arena Elo": 0.15, "Average ⬆️": 0.15},
    "general": {"Arena Elo": 0.3, "Average ⬆️": 0.2, "SWE-bench Verified": 0.15, "BBH": 0.15, "IFEval": 0.1, "MMLU-PRO": 0.1},
}


def recommend(task_type: str = "general", min_context: int = 0, require_tools: bool = False, limit: int = 10) -> list[dict]:
    """Score and rank available free models for a given task type."""
    models = get_models_with_benchmarks(available_only=True)
    caps = get_all_capabilities()
    prefs = get_preferences(task_type)

    pinned = {p["model_id"] for p in prefs if p["action"] == "pin"}
    blocked = {p["model_id"] for p in prefs if p["action"] == "block"}
    boosts = {p["model_id"]: p["weight"] for p in prefs if p["action"] == "boost"}

    weights = TASK_BENCHMARK_WEIGHTS.get(task_type, TASK_BENCHMARK_WEIGHTS["general"])

    scored = []
    for m in models:
        mid = m["id"]
        if mid in blocked:
            continue
        if min_context and m["context_length"] < min_context:
            continue
        if require_tools:
            model_caps = caps.get(mid, {})
            if model_caps.get("supports_tools") != "True":
                continue

        # Compute weighted benchmark score (normalize Arena Elo to 0-100 scale)
        bench_score = 0.0
        bench_count = 0
        for bench, w in weights.items():
            if bench in m.get("benchmarks", {}):
                raw = m["benchmarks"][bench]["score"]
                # Normalize Arena Elo (1000-1400 range) to 0-100
                if bench.startswith("Arena"):
                    normalized = max(0, min(100, (raw - 1000) / 4))
                else:
                    normalized = raw
                bench_score += normalized * w
                bench_count += 1

        # Normalize: if we only matched some benchmarks, scale up
        if bench_count > 0 and bench_count < len(weights):
            total_weight = sum(weights[b] for b in weights if b in m.get("benchmarks", {}))
            if total_weight > 0:
                bench_score = bench_score / total_weight

        # Apply user boost
        if mid in boosts:
            bench_score *= boosts[mid]

        # Context length bonus (0-5 points for models with >100k context)
        ctx_bonus = min(5.0, m["context_length"] / 200000) if m["context_length"] > 100000 else 0

        # Capability bonuses
        model_caps = caps.get(mid, {})
        cap_bonus = 0
        if model_caps.get("supports_tools") == "True":
            cap_bonus += 2
        if model_caps.get("supports_reasoning") == "True":
            cap_bonus += 2

        total = bench_score + ctx_bonus + cap_bonus
        is_pinned = mid in pinned

        scored.append({
            "model_id": mid,
            "name": m["name"],
            "provider": m["provider"],
            "context_length": m["context_length"],
            "score": round(total, 2),
            "bench_score": round(bench_score, 2),
            "benchmarks": m.get("benchmarks", {}),
            "capabilities": model_caps,
            "pinned": is_pinned,
        })

    # Sort: pinned first, then by score
    scored.sort(key=lambda x: (x["pinned"], x["score"]), reverse=True)
    return scored[:limit]


# ── Signals / Scans (unchanged) ────────────────────────────────────────────

def upsert_signals(signals: list[dict]) -> int:
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
    benchmarked = conn.execute("SELECT COUNT(DISTINCT model_id) FROM benchmarks").fetchone()[0]
    last_scan = conn.execute("SELECT finished_at FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return {
        "models_available": models_available,
        "models_total": models_total,
        "signals_total": signals_total,
        "scans_total": scans_total,
        "benchmarked": benchmarked,
        "last_scan": last_scan[0] if last_scan else None,
    }


# Auto-init on import
init_db()
