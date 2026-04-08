# LLM Credit Hunter

Dashboard that tracks free LLM models, benchmarks, and credit opportunities across providers. Scans daily, surfaces new signals, and recommends models by task.

## Features

- **Automated scanning** — daily cron scrapes provider pages, Hacker News, blogs, and GitHub for free-tier changes
- **Model catalog** — tracks availability, context window, tool support, and pricing across providers
- **Benchmark tracking** — stores and displays performance scores (MMLU, HumanEval, MATH, etc.)
- **Smart recommendations** — ranked model suggestions by task type (coding, general, math, creative) with preference learning
- **Signal feed** — surfaces new free-tier announcements, credit opportunities, and provider changes
- **Web dashboard** — single-page overview of models, signals, scan history, and stats
- **REST API** — JSON endpoints for models, signals, scans, benchmarks, recommendations, and preferences

## Quick Start

```bash
pip install fastapi uvicorn apscheduler requests beautifulsoup4 feedparser jinja2 pydantic
python app.py
```

Open `http://localhost:4050` in your browser.

## Project Structure

```
app.py                  → FastAPI server, scheduler, API routes
scanner.py              → Web scrapers (providers, HN, blogs, GitHub)
db.py                   → SQLite storage, queries, recommendation engine
templates/index.html    → Dashboard template (Jinja2)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/models` | List tracked models (filterable) |
| `GET` | `/api/signals` | Recent free-tier signals |
| `GET` | `/api/stats` | Scan stats and totals |
| `GET` | `/api/scans` | Recent scan history |
| `POST` | `/api/scan` | Trigger a manual scan |
| `GET` | `/api/benchmarks` | Benchmark scores (optional model filter) |
| `GET` | `/api/recommend` | Task-based model recommendations |
| `GET/POST/DELETE` | `/api/preferences` | Manage recommendation preferences |

## How It Works

The scanner module scrapes provider pricing pages, Hacker News, RSS feeds, and GitHub repos for mentions of free LLM access. Results are deduplicated and stored in a local SQLite database. The recommendation engine weights benchmark scores by task type and applies user preference boosts.

## License

MIT