#!/usr/bin/env python3
"""
LLM Credit Hunter — web dashboard & API.

Serves a dashboard of free LLM models, benchmarks, and credit opportunities.
Runs a background scheduler for daily scans.
"""

import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

import db
import scanner

APP_DIR = Path(__file__).parent
_jinja_env = Environment(loader=FileSystemLoader(str(APP_DIR / "templates")), auto_reload=True, cache_size=0)

_scan_lock = threading.Lock()
_scan_running = False


def _run_scan():
    global _scan_running
    if not _scan_lock.acquire(blocking=False):
        return {"error": "Scan already in progress"}
    _scan_running = True
    try:
        started = datetime.now().isoformat()
        results = scanner.run_full_scan()

        model_stats = db.upsert_models(results["models"])
        bench_count = db.upsert_benchmarks(results.get("benchmarks", []))

        all_signals = []
        for p in results["providers"]:
            all_signals.append({"source": p["source"], "url": p["url"], "title": p["source"], "snippets": p.get("snippets", [])})
        for h in results["hackernews"]:
            all_signals.append(h)
        for b in results["blogs"]:
            all_signals.append(b)
        for g in results["github"]:
            all_signals.append({"source": g["source"], "url": g["url"], "title": g["source"], "snippets": g.get("providers", [])})

        new_signals = db.upsert_signals(all_signals)
        scan_id = db.record_scan(started, len(results["models"]), len(all_signals), model_stats["new"], new_signals)

        return {
            "scan_id": scan_id,
            "models": len(results["models"]),
            "new_models": model_stats["new"],
            "disappeared": model_stats["disappeared"],
            "benchmarks": bench_count,
            "signals": len(all_signals),
            "new_signals": new_signals,
        }
    finally:
        _scan_running = False
        _scan_lock.release()


sched = BackgroundScheduler()
sched.add_job(_run_scan, "cron", hour=8, minute=13, id="daily_scan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched.start()
    if db.get_stats()["scans_total"] == 0:
        threading.Thread(target=_run_scan, daemon=True).start()
    yield
    sched.shutdown()


ROOT_PATH = os.environ.get("ROOT_PATH", "")
app = FastAPI(title="LLM Credit Hunter", lifespan=lifespan, root_path=ROOT_PATH)


# ── HTML Dashboard ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    template = _jinja_env.get_template("index.html")
    html = template.render(
        stats=db.get_stats(),
        models=db.get_models_with_benchmarks(),
        signals=db.get_signals(limit=50),
        scans=db.get_recent_scans(5),
        scanning=_scan_running,
        preferences=db.get_preferences(),
        task_types=list(db.TASK_BENCHMARK_WEIGHTS.keys()),
    )
    return HTMLResponse(html)


# ── JSON API ────────────────────────────────────────────────────────────────

@app.get("/api/models")
async def api_models(available_only: bool = True):
    return db.get_models_with_benchmarks(available_only)


@app.get("/api/signals")
async def api_signals(limit: int = 100):
    return db.get_signals(limit)


@app.get("/api/stats")
async def api_stats():
    return {**db.get_stats(), "scanning": _scan_running}


@app.get("/api/scans")
async def api_scans(limit: int = 10):
    return db.get_recent_scans(limit)


@app.post("/api/scan")
async def api_scan():
    if _scan_running:
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)
    threading.Thread(target=_run_scan, daemon=True).start()
    return {"status": "started"}


# ── Benchmarks API ──────────────────────────────────────────────────────────

@app.get("/api/benchmarks")
async def api_benchmarks(model_id: str = None):
    return db.get_benchmarks(model_id)


# ── Recommend API ───────────────────────────────────────────────────────────

@app.get("/api/recommend")
async def api_recommend(task: str = "general", min_context: int = 0, require_tools: bool = False, limit: int = 10):
    return db.recommend(task, min_context, require_tools, limit)


# ── Preferences API ─────────────────────────────────────────────────────────

class PrefBody(BaseModel):
    task_type: str
    model_id: str
    action: str = "boost"
    weight: float = 1.0


@app.get("/api/preferences")
async def api_preferences(task_type: str = None):
    return db.get_preferences(task_type)


@app.post("/api/preferences")
async def api_set_preference(body: PrefBody):
    pref_id = db.set_preference(body.task_type, body.model_id, body.action, body.weight)
    return {"id": pref_id}


@app.delete("/api/preferences/{pref_id}")
async def api_delete_preference(pref_id: int):
    db.delete_preference(pref_id)
    return {"deleted": pref_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4050, log_level="info")
