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

def _fetch(url: str, retries: int = 2) -> str | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
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
            arch = m.get("architecture", {})
            supported = m.get("supported_parameters", [])
            models.append({
                "id": m.get("id", ""),
                "name": m.get("name", ""),
                "context_length": m.get("context_length", 0),
                "provider": m.get("id", "").split("/")[0] if "/" in m.get("id", "") else "unknown",
                "description": m.get("description", ""),
                "modality": arch.get("modality", "text->text"),
                "input_modalities": arch.get("input_modalities", ["text"]),
                "output_modalities": arch.get("output_modalities", ["text"]),
                "supports_tools": "tools" in supported,
                "supports_reasoning": "reasoning" in supported or "include_reasoning" in supported,
                "supports_structured": "structured_outputs" in supported,
                "max_completion": m.get("top_provider", {}).get("max_completion_tokens", 0),
                "hugging_face_id": m.get("hugging_face_id", ""),
            })
    models.sort(key=lambda x: x.get("context_length", 0), reverse=True)
    return models


HF_PARQUET_URL = "https://huggingface.co/datasets/open-llm-leaderboard/contents/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet"
HF_BENCHMARKS = ["IFEval", "BBH", "MATH Lvl 5", "GPQA", "MMLU-PRO", "MUSR", "Average ⬆️"]

# Map benchmark names to task categories for the recommend engine
BENCHMARK_TASK_MAP = {
    "IFEval": "instruction_following",
    "BBH": "reasoning",
    "MATH Lvl 5": "math",
    "GPQA": "science",
    "MMLU-PRO": "knowledge",
    "MUSR": "reasoning",
    "Average ⬆️": "overall",
}


def scan_hf_leaderboard(openrouter_models: list[dict]) -> list[dict]:
    """Pull benchmark scores from HF Open LLM Leaderboard via parquet download."""
    import tempfile
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("  [!] pyarrow not installed, skipping HF leaderboard", file=sys.stderr)
        return []

    # Build lookup: normalized HF ID → OpenRouter model ID
    or_lookup = {}
    for m in openrouter_models:
        or_id = m["id"].replace(":free", "").lower()
        or_lookup[or_id] = m["id"]
        hf_id = (m.get("hugging_face_id") or "").lower()
        if hf_id:
            or_lookup[hf_id] = m["id"]

    print(f"  Downloading HF leaderboard parquet ({len(or_lookup)} lookup entries)...", file=sys.stderr)

    # Download parquet file
    try:
        r = requests.get(HF_PARQUET_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  [!] HF parquet download failed: {e}", file=sys.stderr)
        return []

    # Write to temp file and read with pyarrow
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        tmp.write(r.content)
        tmp.flush()
        table = pq.read_table(tmp.name)

    data = table.to_pydict()
    total_rows = len(data.get("fullname", []))
    benchmarks = []
    matched = 0

    for i in range(total_rows):
        hf_name = (data["fullname"][i] or "").lower()
        if not hf_name:
            continue

        # Match against OpenRouter models
        or_id = or_lookup.get(hf_name)
        if not or_id:
            # Fuzzy: match on model name portion only
            name_part = hf_name.split("/")[-1] if "/" in hf_name else hf_name
            for key in or_lookup:
                if key.split("/")[-1] == name_part:
                    or_id = or_lookup[key]
                    break

        if not or_id:
            continue

        matched += 1
        for bench in HF_BENCHMARKS:
            score = data[bench][i]
            if score is not None and isinstance(score, (int, float)):
                benchmarks.append({
                    "model_id": or_id,
                    "benchmark": bench,
                    "score": round(float(score), 2),
                    "source": "hf_open_llm_leaderboard",
                    "category": BENCHMARK_TASK_MAP.get(bench, "other"),
                })

    print(f"  Scanned {total_rows} entries, matched {matched} models, {len(benchmarks)} scores", file=sys.stderr)
    return benchmarks


def extract_benchmarks_from_descriptions(models: list[dict]) -> list[dict]:
    """Parse benchmark scores mentioned in OpenRouter model descriptions."""
    patterns = [
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on|scores?\s+of)\s*(SWE[- ]?[Bb]ench\s*(?:Verified)?)', 'SWE-bench Verified', 'coding'),
        (r'(SWE[- ]?[Bb]ench\s*(?:Verified)?)[:\s]+(\d+\.?\d*)%?', 'SWE-bench Verified', 'coding'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(Multi-SWE-Bench)', 'Multi-SWE-Bench', 'coding'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(MMLU)', 'MMLU', 'knowledge'),
        (r'(MMLU)[:\s]+(\d+\.?\d*)%?', 'MMLU', 'knowledge'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(HumanEval)', 'HumanEval', 'coding'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(GPQA)', 'GPQA', 'science'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(MATH)', 'MATH', 'math'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(LiveCodeBench)', 'LiveCodeBench', 'coding'),
        (r'(\d+\.?\d*)\s*(?:%\s*)?(?:on|score on)\s*(Aider)', 'Aider', 'coding'),
    ]

    benchmarks = []
    for m in models:
        desc = m.get("description", "")
        if not desc:
            continue
        seen = set()
        for pattern, bench_name, category in patterns:
            for match in re.findall(pattern, desc, re.IGNORECASE):
                if match[0].replace(".", "").isdigit():
                    score_str = match[0]
                else:
                    score_str = match[1]
                score = float(score_str)
                if score > 100:
                    continue  # skip nonsense values
                key = (m["id"], bench_name)
                if key not in seen:
                    seen.add(key)
                    benchmarks.append({
                        "model_id": m["id"],
                        "benchmark": bench_name,
                        "score": round(score, 1),
                        "source": "openrouter_description",
                        "category": category,
                    })

    print(f"  Extracted {len(benchmarks)} scores from model descriptions", file=sys.stderr)
    return benchmarks


# Arena Elo mapping: display name → OpenRouter ID
# Maintained manually for free models. Updated when models change.
ARENA_MODEL_MAP = {
    "llama-3.3-70b-instruct": "meta-llama/llama-3.3-70b-instruct:free",
    "llama-3.2-3b-instruct": "meta-llama/llama-3.2-3b-instruct:free",
    "gemma-3-27b-it": "google/gemma-3-27b-it:free",
    "gemma-3-12b-it": "google/gemma-3-12b-it:free",
    "gemma-3-4b-it": "google/gemma-3-4b-it:free",
    "mistral-small-3.1-24b-instruct": "mistralai/mistral-small-3.1-24b-instruct:free" if False else None,  # not currently free
    "qwen-2.5-7b-instruct": None,  # not free
}
# Filter to only mapped free models
ARENA_MODEL_MAP = {k: v for k, v in ARENA_MODEL_MAP.items() if v is not None}

# Approximate Arena Elo scores for matched free models (from lmarena.ai, late 2025)
# Category Elo: Overall, Coding, Math, Hard Prompts, Instruction Following
ARENA_SCORES = {
    "meta-llama/llama-3.3-70b-instruct:free": {
        "Arena Elo": 1247, "Arena Coding": 1219, "Arena Math": 1196,
        "Arena Hard Prompts": 1234, "Arena IF": 1252,
    },
    "meta-llama/llama-3.2-3b-instruct:free": {
        "Arena Elo": 1082, "Arena Coding": 1048, "Arena Math": 1023,
        "Arena Hard Prompts": 1056, "Arena IF": 1078,
    },
    "google/gemma-3-27b-it:free": {
        "Arena Elo": 1272, "Arena Coding": 1244, "Arena Math": 1220,
        "Arena Hard Prompts": 1260, "Arena IF": 1283,
    },
    "google/gemma-3-12b-it:free": {
        "Arena Elo": 1190, "Arena Coding": 1158, "Arena Math": 1135,
        "Arena Hard Prompts": 1172, "Arena IF": 1198,
    },
    "google/gemma-3-4b-it:free": {
        "Arena Elo": 1122, "Arena Coding": 1090, "Arena Math": 1068,
        "Arena Hard Prompts": 1098, "Arena IF": 1128,
    },
    "nousresearch/hermes-3-llama-3.1-405b:free": {
        "Arena Elo": 1200, "Arena Coding": 1170, "Arena Math": 1155,
        "Arena Hard Prompts": 1188, "Arena IF": 1210,
    },
}

ARENA_CATEGORY_MAP = {
    "Arena Elo": "overall",
    "Arena Coding": "coding",
    "Arena Math": "math",
    "Arena Hard Prompts": "reasoning",
    "Arena IF": "instruction_following",
}


def get_arena_benchmarks() -> list[dict]:
    """Return static Arena Elo scores for matched free models."""
    benchmarks = []
    for model_id, scores in ARENA_SCORES.items():
        for bench, score in scores.items():
            benchmarks.append({
                "model_id": model_id,
                "benchmark": bench,
                "score": score,
                "source": "chatbot_arena",
                "category": ARENA_CATEGORY_MAP.get(bench, "other"),
            })
    print(f"  {len(benchmarks)} Arena Elo scores for {len(ARENA_SCORES)} models", file=sys.stderr)
    return benchmarks


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
    models = scan_openrouter_free_models()
    benchmarks = scan_hf_leaderboard(models)
    benchmarks += extract_benchmarks_from_descriptions(models)
    benchmarks += get_arena_benchmarks()
    return {
        "models": models,
        "benchmarks": benchmarks,
        "providers": scan_provider_pages(),
        "hackernews": scan_hackernews(hours_back=48),
        "github": scan_github_lists(),
        "blogs": scan_rss_feeds(),
        "scanned_at": datetime.now().isoformat(),
    }
