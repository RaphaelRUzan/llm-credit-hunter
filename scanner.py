"""
LLM Credit Hunter — scanner module.

Importable by the web app. Each scan_* function returns structured dicts.
run_full_scan() orchestrates all scanners and returns the combined result.
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}
TIMEOUT = 15

KEYWORDS = [
    "free credits", "free tier", "api credits", "promotional credits",
    "startup credits", "research credits", "free api", "free access",
    "credit grant", "developer program", "builders program",
    "trial credits", "bonus credits", "open access", "no cost",
]

PROVIDER_PAGES = [
    {"name": "Anthropic", "urls": ["https://www.anthropic.com/pricing", "https://docs.anthropic.com/en/docs/about-claude/models"], "extra_keywords": ["free", "credit", "trial", "build program"]},
    {"name": "OpenAI", "urls": ["https://openai.com/api/pricing/", "https://platform.openai.com/docs/overview"], "extra_keywords": ["free", "credit", "grant", "startup"]},
    {"name": "Google AI", "urls": ["https://ai.google.dev/pricing", "https://cloud.google.com/free"], "extra_keywords": ["free", "credit", "no charge", "trial"]},
    {"name": "AWS Bedrock", "urls": ["https://aws.amazon.com/bedrock/pricing/", "https://aws.amazon.com/activate/"], "extra_keywords": ["free tier", "credit", "activate"]},
    {"name": "Azure OpenAI", "urls": ["https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/", "https://www.microsoft.com/en-us/startups"], "extra_keywords": ["free", "credit", "founders hub"]},
    {"name": "Mistral", "urls": ["https://mistral.ai/products/la-plateforme/"], "extra_keywords": ["free", "credit", "tier"]},
    {"name": "Together AI", "urls": ["https://www.together.ai/pricing"], "extra_keywords": ["free", "credit"]},
    {"name": "Groq", "urls": ["https://groq.com/pricing/"], "extra_keywords": ["free", "credit"]},
    {"name": "Fireworks AI", "urls": ["https://fireworks.ai/pricing"], "extra_keywords": ["free", "credit"]},
    {"name": "OpenRouter", "urls": ["https://openrouter.ai/pricing", "https://openrouter.ai/docs/api-reference/limits"], "extra_keywords": ["free", "credit", "free model", "no cost", "rate limit"]},
]

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

GITHUB_REPOS = [
    {"name": "cheahjs/free-llm-api-resources", "readme_url": "https://raw.githubusercontent.com/cheahjs/free-llm-api-resources/main/README.md", "commits_url": "https://api.github.com/repos/cheahjs/free-llm-api-resources/commits?per_page=10"},
    {"name": "mnfst/awesome-free-llm-apis", "readme_url": "https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/README.md", "commits_url": "https://api.github.com/repos/mnfst/awesome-free-llm-apis/commits?per_page=10"},
]

RSS_FEEDS = [
    ("Anthropic Blog", "https://www.anthropic.com/rss.xml"),
    ("OpenAI Blog", "https://openai.com/blog/rss.xml"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("AWS Blog", "https://aws.amazon.com/blogs/aws/feed/"),
    ("Azure Blog", "https://azure.microsoft.com/en-us/blog/feed/"),
    ("Mistral Blog", "https://mistral.ai/feed.xml"),
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [!] Failed: {url}: {e}", file=sys.stderr)
        return None


def _text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True).lower()


def _find_snippets(text: str, keywords: list[str]) -> list[str]:
    snippets = []
    for s in re.split(r'[.!?\n]+', text):
        s = s.strip()
        if len(s) < 10 or len(s) > 500:
            continue
        for kw in keywords:
            if kw in s:
                snippets.append(s[:300])
                break
    return list(set(snippets))[:10]


# ── Scanners ────────────────────────────────────────────────────────────────

def scan_openrouter_free_models() -> list[dict]:
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [!] OpenRouter API error: {e}", file=sys.stderr)
        return []

    models = []
    for m in data.get("data", []):
        pricing = m.get("pricing", {})
        if float(pricing.get("prompt", "1") or "1") == 0 and float(pricing.get("completion", "1") or "1") == 0:
            models.append({
                "id": m.get("id", ""),
                "name": m.get("name", ""),
                "context_length": m.get("context_length", 0),
                "provider": m.get("id", "").split("/")[0] if "/" in m.get("id", "") else "unknown",
            })
    models.sort(key=lambda x: x.get("context_length", 0), reverse=True)
    return models


def scan_provider_pages() -> list[dict]:
    findings = []
    for provider in PROVIDER_PAGES:
        all_kw = KEYWORDS + provider.get("extra_keywords", [])
        for url in provider["urls"]:
            html = _fetch(url)
            if not html:
                continue
            snippets = _find_snippets(_text_from_html(html), all_kw)
            if snippets:
                findings.append({"source": provider["name"], "url": url, "snippets": snippets})
            time.sleep(0.5)
    return findings


def scan_hackernews(hours_back: int = 48) -> list[dict]:
    findings = []
    queries = ["free LLM credits", "free API credits", "AI startup credits", "free tier LLM", "developer credits AI"]
    seen = set()
    cutoff = int((datetime.now() - timedelta(hours=hours_back)).timestamp())

    for q in queries:
        url = f"{HN_SEARCH_URL}?query={quote_plus(q)}&tags=story&numericFilters=created_at_i>{cutoff}"
        raw = _fetch(url)
        if not raw:
            continue
        try:
            for hit in json.loads(raw).get("hits", []):
                story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                if story_url in seen:
                    continue
                seen.add(story_url)
                findings.append({
                    "source": "Hacker News", "title": hit.get("title", ""),
                    "url": story_url, "points": hit.get("points", 0),
                    "created": hit.get("created_at", "")[:10],
                })
        except (json.JSONDecodeError, KeyError):
            pass
        time.sleep(0.5)
    return findings


def scan_github_lists() -> list[dict]:
    findings = []
    cutoff = datetime.now() - timedelta(days=7)

    for repo in GITHUB_REPOS:
        commits_raw = _fetch(repo["commits_url"])
        recent_changes = []
        if commits_raw:
            try:
                for c in json.loads(commits_raw):
                    msg = c.get("commit", {}).get("message", "")
                    date_str = c.get("commit", {}).get("committer", {}).get("date", "")
                    if date_str:
                        try:
                            if datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None) < cutoff:
                                continue
                        except ValueError:
                            pass
                    if msg:
                        recent_changes.append(msg.split("\n")[0][:200])
            except (json.JSONDecodeError, KeyError):
                pass

        readme = _fetch(repo["readme_url"])
        providers = []
        if readme:
            for line in readme.split("\n"):
                ll = line.lower().strip()
                if "|" in line and any(kw in ll for kw in ["free", "✅", "✓", "yes", "unlimited", "no cost", "$0"]):
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    if cells and not cells[0].startswith("---"):
                        providers.append(" | ".join(cells)[:200])
                elif line.strip().startswith(("- ", "* ", "1.")) and any(kw in ll for kw in ["free", "api", "credit", "no cost"]):
                    providers.append(line.strip().lstrip("-*").strip()[:200])

        if recent_changes or providers:
            findings.append({
                "source": repo["name"], "url": f"https://github.com/{repo['name']}",
                "recent_changes": recent_changes[:10], "providers": providers[:30],
            })
        time.sleep(0.5)
    return findings


def scan_rss_feeds() -> list[dict]:
    findings = []
    for name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:
                combined = f"{entry.get('title', '')} {entry.get('summary', '')[:500]}".lower()
                if any(kw in combined for kw in KEYWORDS):
                    findings.append({
                        "source": name, "title": entry.get("title", ""),
                        "url": entry.get("link", ""), "published": entry.get("published", "")[:10],
                    })
        except Exception as e:
            print(f"  [!] RSS error {name}: {e}", file=sys.stderr)
        time.sleep(0.3)
    return findings


# ── Orchestrator ────────────────────────────────────────────────────────────

def run_full_scan() -> dict:
    """Run all scanners and return structured results."""
    return {
        "models": scan_openrouter_free_models(),
        "providers": scan_provider_pages(),
        "hackernews": scan_hackernews(hours_back=48),
        "github": scan_github_lists(),
        "blogs": scan_rss_feeds(),
        "scanned_at": datetime.now().isoformat(),
    }
