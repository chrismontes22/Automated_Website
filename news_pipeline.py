"""
News Pipeline
=============
Fetches, curates, scrapes, and summarises technology and financial news articles.
Configuration is loaded exclusively from config.yaml — no built-in defaults exist,
so the file must be present before the pipeline can run.

Designed for resilience: retries failed steps, saves progress after each article,
and resumes cleanly from a previous interrupted run.

After each successful summary a second Gemini call classifies the article into one
of the categories defined in config.yaml.  Articles labelled "Other" (or returning
any unrecognised string) do NOT count toward the success goal.

Persistent logs (never deleted):
  category_log.json  – machine-readable record of every run
  category_log.txt   – human-readable; cumulative totals at top, runs below
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional

import requests
import trafilatura
import yaml
from dotenv import load_dotenv

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

load_dotenv()


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("news_pipeline")


def log_error(context: str, exc: Exception) -> None:
    """Emit a consistently formatted ERROR with context and exception type."""
    log.error("[%s] %s: %s", context, type(exc).__name__, exc)


def log_warning(context: str, reason: str) -> None:
    """Emit a consistently formatted WARNING."""
    log.warning("[%s] %s", context, reason)


# =============================================================================
# CLASSIFICATION CONSTANTS
# =============================================================================

# The fallback label — articles receiving this (or any unrecognised string) are rejected.
CATEGORY_OTHER = "Other"


# =============================================================================
# RETRY DECORATOR
# =============================================================================

def with_retry(attempts: int = 3, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    """
    Decorator factory: re-runs the wrapped function up to `attempts` times on
    failure, waiting backoff * 2^n seconds between tries.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            delay = backoff
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    log.warning(
                        "[retry] %s attempt %d/%d failed — %s: %s — retrying in %.1fs",
                        fn.__name__, attempt, attempts, type(exc).__name__, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
        return wrapper
    return decorator


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class PipelineConfig:
    """
    All pipeline settings.  Every field is required — there are no built-in
    defaults.  Always construct via PipelineConfig.from_yaml().
    """
    # NewsAPI
    topics: list[str]
    domains: str
    page_size: int
    sort_by: str
    hours_back_from: int
    hours_back_to: int

    # Curation
    candidate_count: int
    source_limit: int

    # Scraping
    min_content_length: int
    chunk_size: int
    scrape_timeout: int

    # AI / Summarisation
    gemini_model: str
    max_output_tokens: int
    temperature: float

    # AI / Classification
    classifier_model: str
    classifier_delay: float

    # Categories (loaded from config; determines what the classifier accepts)
    valid_categories: list[str]

    # Pipeline control
    success_goal: int
    retry_attempts: int
    retry_backoff: float
    inter_request_delay: float

    # Paths
    output_dir: Path

    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path = "config.yaml") -> "PipelineConfig":
        """
        Load and validate config.yaml.  Raises FileNotFoundError if the file
        is absent, and KeyError if a required section is missing.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {p.resolve()}\n"
                "Create config.yaml (see the project README) before running the pipeline."
            )

        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Fail fast with a clear message for each missing top-level section.
        required_sections = ("news_api", "curation", "scraping", "ai", "pipeline", "categories", "paths")
        for section in required_sections:
            if section not in raw:
                raise KeyError(
                    f"config.yaml is missing required section '{section}'. "
                    "Check your config.yaml against the template."
                )

        na    = raw["news_api"]
        cur   = raw["curation"]
        scr   = raw["scraping"]
        ai    = raw["ai"]
        pip   = raw["pipeline"]
        cats  = raw["categories"]
        paths = raw["paths"]

        if not cats:
            raise ValueError("config.yaml 'categories' list must not be empty.")

        return cls(
            topics              = list(na["topics"]),
            domains             = str(na.get("domains", "")),
            page_size           = int(na["page_size"]),
            sort_by             = str(na["sort_by"]),
            hours_back_from     = int(na["hours_back_from"]),
            hours_back_to       = int(na["hours_back_to"]),
            candidate_count     = int(cur["candidate_count"]),
            source_limit        = int(cur["source_limit"]),
            min_content_length  = int(scr["min_content_length"]),
            chunk_size          = int(scr["chunk_size"]),
            scrape_timeout      = int(scr["scrape_timeout"]),
            gemini_model        = str(ai["summarizer_model"]),
            max_output_tokens   = int(ai["max_output_tokens"]),
            temperature         = float(ai["temperature"]),
            classifier_model    = str(ai["classifier_model"]),
            classifier_delay    = float(ai["classifier_delay"]),
            valid_categories    = list(cats),
            success_goal        = int(pip["success_goal"]),
            retry_attempts      = int(pip["retry_attempts"]),
            retry_backoff       = float(pip["retry_backoff"]),
            inter_request_delay = float(pip["inter_request_delay"]),
            output_dir          = Path(paths["output_dir"]),
        )

    # ------------------------------------------------------------------
    # Derived paths — all respect output_dir
    @property
    def master_file(self) -> Path:
        return self.output_dir / "master_news.json"

    @property
    def progress_file(self) -> Path:
        return self.output_dir / "pipeline_progress.json"

    @property
    def results_file(self) -> Path:
        return self.output_dir / "processed_articles.json"

    @property
    def category_log_json(self) -> Path:
        return self.output_dir / "category_log.json"

    @property
    def category_log_txt(self) -> Path:
        return self.output_dir / "category_log.txt"


# =============================================================================
# PROMPTS
# =============================================================================

SUMMARY_PROMPT = """\
You are a news analyst writing SEO-friendly summaries for a website.

Write a concise, factual summary for a general audience. Keep the tone neutral, readable, and publication-ready. Use the article's main subject and important entities naturally in the wording, but do not add keywords unnaturally or repeat phrases.

Follow this exact structure and nothing else:

**One-sentence headline summary (Don't write 'One-sentence headline summary', just write the sentence itself)**
- 1 sentence only
- 20 to 30 words
- Must clearly state the main topic and the most important entity/event
- Write it so it works well as a search-snippet style summary

**Key points**
- Exactly 3 to 6 bullet points
- Each bullet should be short and information-dense
- Include concrete facts, names, numbers, products, companies, dates, or locations when present
- Prefer specific nouns and verbs over vague language

**Why it matters**
- One bullet point only
- Exactly 2 to 3 sentences
- Explain the broader impact, business implication, market relevance, or user relevance
- Keep it plain-English and objective

Style rules:
- Do not use emojis
- Do not include a preface, disclaimer, or closing line
- Do not mention that you are an AI
- Do not invent facts
- Do not change the headings
- Do not add any sections beyond the three specified above

Article:
{text}
"""


def build_classifier_prompt(title: str, summary: str, valid_categories: list[str]) -> str:
    """Build the classifier prompt dynamically from the configured category list."""
    all_cats = valid_categories + [CATEGORY_OTHER]
    cats_block = "\n".join(f"  {c}" for c in all_cats)
    return (
        "You are a news classification agent. Your only job is to read the article "
        "information below and output exactly one of these category labels — nothing "
        "else, no explanation, no punctuation, no extra words:\n\n"
        + cats_block
        + f'\n\nUse "{CATEGORY_OTHER}" only if the article does not clearly fit any of '
        "the specific categories above.\n\n"
        f"Article title: {title}\n\n"
        f"Article summary:\n{summary}"
    )


# =============================================================================
# ARTICLE FETCHER
# =============================================================================

class ArticleFetcher:
    """Fetches raw news articles from NewsAPI for each configured topic (search query)."""

    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("NEWS_API_KEY")
        # Wrap the implementation with retry using config values.
        self._fetch_one = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(requests.RequestException,),
        )(self._fetch_one_impl)

    def _validate(self) -> None:
        if not self._api_key:
            raise EnvironmentError(
                "NEWS_API_KEY is not set. Add it to your .env file and restart."
            )

    def _time_window(self) -> tuple[str, str]:
        now = datetime.utcnow()
        from_dt = (now - timedelta(hours=self.cfg.hours_back_from)).isoformat()
        to_dt   = (now - timedelta(hours=self.cfg.hours_back_to)).isoformat()
        return from_dt, to_dt

    def _fetch_one_impl(self, topic: str, from_dt: str, to_dt: str) -> dict:
        params = {
            "q":        topic,
            "from":     from_dt,
            "to":       to_dt,
            "language": "en",
            "domains":  self.cfg.domains,
            "pageSize": self.cfg.page_size,
            "sortBy":   self.cfg.sort_by,
            "apiKey":   self._api_key,
        }
        resp = requests.get(self.BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def fetch_all(self) -> list[Path]:
        """Fetch every topic query and persist each response to its own JSON file."""
        self._validate()
        from_dt, to_dt = self._time_window()
        log.info("Fetch window: %s → %s", from_dt, to_dt)

        saved: list[Path] = []
        for topic in self.cfg.topics:
            log.info("Fetching topic: %s", topic)
            try:
                data = self._fetch_one(topic, from_dt, to_dt)
                count = len(data.get("articles", []))
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                path = self.cfg.output_dir / f"news_{topic.replace(' ', '_')}_{ts}.json"
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                log.info("  ✓ %d articles saved → %s", count, path.name)
                saved.append(path)
            except Exception as exc:
                log_error(f"fetch:{topic}", exc)

        log.info("Fetch complete: %d/%d topics saved.", len(saved), len(self.cfg.topics))
        return saved


# =============================================================================
# MASTER BUILDER
# =============================================================================

class MasterBuilder:
    """
    Merges per-topic JSON files into one curated, deduplicated master list.

    Strategy: round-robin across topics so no single topic dominates, with a
    hard cap of `source_limit` articles per publisher.  Deduplication covers
    URL, title, and author to block reposts across sources.
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg

    def _load_topic_queues(self) -> list[list[dict]]:
        files = sorted(
            self.cfg.output_dir.glob("news_*.json"),
            key=lambda p: p.stat().st_ctime,
            reverse=True,
        )
        if not files:
            raise FileNotFoundError(
                "No news_*.json files found. The fetch step may have failed."
            )

        queues = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                articles = data.get("articles") or []
                if articles:
                    queues.append(articles)
                    log.debug("  Loaded %d articles from %s", len(articles), path.name)
            except (json.JSONDecodeError, OSError) as exc:
                log_warning(
                    f"build:load:{path.name}",
                    f"Skipping unreadable file — {type(exc).__name__}: {exc}",
                )

        return queues

    @staticmethod
    def _source_name(article: dict) -> str:
        return article.get("source", {}).get("name") or "Unknown"

    def build(self) -> dict:
        queues = self._load_topic_queues()
        master: list[dict] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        seen_authors: set[str] = set()
        source_counts: dict[str, int] = {}
        goal = self.cfg.candidate_count

        max_rows = max(len(q) for q in queues)
        for i in range(max_rows):
            for queue in queues:
                if i >= len(queue) or len(master) >= goal:
                    continue
                article = queue[i]
                url    = (article.get("url") or "").strip()
                title  = (article.get("title") or "").strip()
                author = (article.get("author") or "").strip()
                source = self._source_name(article)

                if not url or url in seen_urls:
                    continue
                if title and title in seen_titles:
                    continue
                if author and author in seen_authors:
                    continue
                if source_counts.get(source, 0) >= self.cfg.source_limit:
                    continue

                master.append(article)
                seen_urls.add(url)
                if title:
                    seen_titles.add(title)
                if author:
                    seen_authors.add(author)
                source_counts[source] = source_counts.get(source, 0) + 1

            if len(master) >= goal:
                break

        output = {
            "status": "ok",
            "totalResults": len(master),
            "articles": master,
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "source_limit": self.cfg.source_limit,
                "candidate_goal": goal,
            },
        }
        self.cfg.master_file.write_text(json.dumps(output, indent=2), encoding="utf-8")

        log.info("Master list: %d unique articles (goal: %d)", len(master), goal)
        dist_lines = [f"  {s}: {c}" for s, c in sorted(source_counts.items(), key=lambda x: -x[1])]
        log.info("Source distribution:\n%s", "\n".join(dist_lines))

        return output

    def load(self) -> dict:
        if not self.cfg.master_file.exists():
            raise FileNotFoundError(
                f"{self.cfg.master_file} not found. The build step may have failed."
            )
        return json.loads(self.cfg.master_file.read_text(encoding="utf-8"))


# =============================================================================
# ARTICLE SCRAPER
# =============================================================================

class ArticleScraper:
    """Fetches and extracts the full body text of an article from its URL."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._download_and_extract = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(Exception,),
        )(self._download_and_extract_impl)

    def _deduplicate(self, text: str) -> str:
        """Remove repeated content blocks (common with scrapers)."""
        cs = self.cfg.chunk_size
        if len(text) <= cs * 2:
            return text
        first_chunk = text[:cs]
        dup_idx = text.find(first_chunk, cs)
        return text[:dup_idx].strip() if dup_idx != -1 else text

    def _download_and_extract_impl(self, url: str) -> str:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError("No content returned — possible network issue or bot block")

        text = trafilatura.extract(
            downloaded,
            favor_precision=True,
            include_tables=True,
            include_comments=False,
        )
        if not text:
            raise ValueError("No text extracted — article is likely paywalled or empty")

        if len(text) < self.cfg.min_content_length:
            raise ValueError(
                f"Content too short ({len(text)} chars, minimum is {self.cfg.min_content_length})"
            )

        return self._deduplicate(text)

    def scrape(self, article: dict) -> tuple[bool, Optional[str], Optional[str]]:
        """Returns (success, content_or_None, error_or_None)."""
        url = (article.get("url") or "").strip()
        if not url:
            return False, None, "Article has no URL"

        try:
            content = self._download_and_extract(url)
            return True, content, None
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"


# =============================================================================
# ARTICLE SUMMARISER
# =============================================================================

class ArticleSummarizer:
    """Summarises article text using Google Gemini."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None
        self._call_api = None

        if not GEMINI_AVAILABLE:
            log_warning("summarizer:init", "google-genai is not installed — summarisation unavailable")
            return
        if not self._api_key:
            log_warning("summarizer:init", "GEMINI_KEY is not set — summarisation unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)
        self._call_api = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(Exception,),
        )(self._call_api_impl)

    def _is_ready(self) -> bool:
        return self._client is not None and self._call_api is not None

    def _call_api_impl(self, text: str) -> str:
        response = self._client.models.generate_content(
            model=self.cfg.gemini_model,
            contents=SUMMARY_PROMPT.format(text=text),
            config={
                "max_output_tokens": self.cfg.max_output_tokens,
                "temperature": self.cfg.temperature,
            },
        )
        summary = (response.text or "").strip()
        if len(summary) < 50:
            raise ValueError(f"Summary suspiciously short ({len(summary)} chars) — possible API issue")
        return summary

    def summarize(self, text: str) -> tuple[bool, Optional[str], Optional[str]]:
        """Returns (success, summary_or_None, error_or_None)."""
        if not self._is_ready():
            return False, None, "Gemini client not initialised (check GEMINI_KEY and google-genai install)"
        if not text or len(text) < 100:
            return False, None, f"Input text too short to summarise ({len(text)} chars)"

        try:
            summary = self._call_api(text)
            return True, summary, None
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"


# =============================================================================
# ARTICLE CLASSIFIER
# =============================================================================

class ArticleClassifier:
    """
    Classifies a summarised article into one of cfg.valid_categories using Gemini.

    Rules:
      • Temperature is always 0.
      • A configurable delay is inserted before each API call to stay within
        the model's requests-per-minute quota.
      • The accepted category list is driven entirely by config.yaml — changing
        categories there is all that is needed.
      • Returns the raw label string; validation against valid_categories is
        done by the pipeline orchestrator.
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None
        self._call_api = None

        if not GEMINI_AVAILABLE:
            log_warning("classifier:init", "google-genai is not installed — classification unavailable")
            return
        if not self._api_key:
            log_warning("classifier:init", "GEMINI_KEY is not set — classification unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)
        self._call_api = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(Exception,),
        )(self._call_api_impl)

    def _is_ready(self) -> bool:
        return self._client is not None and self._call_api is not None

    def _call_api_impl(self, title: str, summary: str) -> str:
        prompt = build_classifier_prompt(title, summary, self.cfg.valid_categories)
        response = self._client.models.generate_content(
            model=self.cfg.classifier_model,
            contents=prompt,
            config={
                "max_output_tokens": 20,
                "temperature": 0,   # always deterministic for classification
            },
        )
        return (response.text or "").strip()

    def classify(
        self, title: str, summary: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Returns (api_ok, label_or_None, error_or_None).

        api_ok is True even when the label is "Other" or unrecognised — it
        only reflects whether the API call itself succeeded.
        """
        if not self._is_ready():
            return False, None, "Gemini classifier not initialised (check GEMINI_KEY and google-genai install)"
        if not summary:
            return False, None, "Empty summary passed to classifier"

        log.info("  [classify] Waiting %.1fs before API call (RPM guard)…", self.cfg.classifier_delay)
        time.sleep(self.cfg.classifier_delay)

        try:
            label = self._call_api(title, summary)
            return True, label, None
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"


# =============================================================================
# PROGRESS TRACKER
# =============================================================================

@dataclass
class ArticleResult:
    index: int
    url: str
    title: str
    source: str
    author: str
    success: bool
    category: Optional[str]       = None
    content: Optional[str]        = None
    summary: Optional[str]        = None
    scrape_error: Optional[str]   = None
    summary_error: Optional[str]  = None
    classify_error: Optional[str] = None
    processed_at: str             = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "ArticleResult":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ProgressTracker:
    """Persists pipeline state so a run can be resumed after a crash."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self.results: dict[int, ArticleResult] = {}
        self._load()

    def _load(self) -> None:
        if not self.cfg.progress_file.exists():
            return
        try:
            raw = json.loads(self.cfg.progress_file.read_text(encoding="utf-8"))
            for d in raw.get("results", []):
                r = ArticleResult.from_dict(d)
                self.results[r.index] = r
            log.info(
                "Resumed from progress file: %d processed (%d successful)",
                len(self.results),
                self.success_count,
            )
        except Exception as exc:
            log_warning(
                "tracker:load",
                f"Could not load progress file — starting fresh. {type(exc).__name__}: {exc}",
            )
            self.results = {}

    def record(self, result: ArticleResult) -> None:
        self.results[result.index] = result
        self._persist()

    def _persist(self) -> None:
        data = {
            "success_count": self.success_count,
            "total_processed": len(self.results),
            "last_updated": datetime.now().isoformat(),
            "results": [r.to_dict() for r in self.results.values()],
        }
        self.cfg.progress_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def processed_indices(self) -> set[int]:
        return set(self.results.keys())

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results.values() if r.success)

    @property
    def successes(self) -> list[ArticleResult]:
        return [r for r in self.results.values() if r.success]


# =============================================================================
# CATEGORY LOG WRITER
# =============================================================================

class CategoryLogWriter:
    """
    Maintains a persistent log of per-run and cumulative category counts.

    category_log.json  — machine-readable; never deleted
    category_log.txt   — human-readable; never deleted; cumulative totals at top
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg

    def _load_history(self) -> list[dict]:
        if not self.cfg.category_log_json.exists():
            return []
        try:
            return json.loads(self.cfg.category_log_json.read_text(encoding="utf-8"))
        except Exception as exc:
            log_warning("category_log:load", f"Could not read log JSON — starting fresh. {exc}")
            return []

    def record_run(self, successes: list[ArticleResult]) -> None:
        """Append this run's category counts and rewrite both log files."""
        counts: dict[str, int] = {cat: 0 for cat in self.cfg.valid_categories}
        for r in successes:
            if r.category in counts:
                counts[r.category] += 1

        run_entry = {
            "run_at": datetime.now().isoformat(),
            "total_successes": len(successes),
            "categories": self.cfg.valid_categories,   # stored so txt can handle config changes
            "category_counts": counts,
        }

        history = self._load_history()
        history.append(run_entry)

        self.cfg.category_log_json.write_text(json.dumps(history, indent=2), encoding="utf-8")
        self._write_txt(history)

        log.info("Category log updated: %d total runs on record.", len(history))

    def _write_txt(self, history: list[dict]) -> None:
        """Write the complete txt log: cumulative totals first, then per-run (newest first)."""
        # Collect all unique categories across all runs (preserving first-seen order).
        seen: set[str] = set()
        all_cats: list[str] = []
        for run in history:
            for cat in run.get("categories", list(run.get("category_counts", {}).keys())):
                if cat not in seen:
                    all_cats.append(cat)
                    seen.add(cat)

        # Cumulative totals across all runs.
        cumulative: dict[str, int] = {cat: 0 for cat in all_cats}
        for run in history:
            for cat, n in run.get("category_counts", {}).items():
                if cat in cumulative:
                    cumulative[cat] += n

        cum_total = sum(cumulative.values())
        lines: list[str] = []

        divider_thick = "═" * 62
        divider_thin  = "─" * 62

        lines += [
            divider_thick,
            "  CUMULATIVE CATEGORY TOTALS  (all runs)",
            divider_thick,
        ]
        for cat in all_cats:
            lines.append(f"  {cat:<32}  {cumulative[cat]:>4}")
        lines += [
            divider_thin,
            f"  {'TOTAL (valid categories)':<32}  {cum_total:>4}",
            divider_thick,
            "",
        ]

        for run in reversed(history):
            run_counts = run.get("category_counts", {})
            run_cats   = run.get("categories", list(run_counts.keys()))
            run_total  = run.get("total_successes", 0)
            run_at     = run.get("run_at", "unknown")
            lines += [
                f"Run  {run_at}   ({run_total} successes)",
                divider_thin,
            ]
            for cat in run_cats:
                lines.append(f"  {cat:<32}  {run_counts.get(cat, 0):>4}")
            lines.append("")

        self.cfg.category_log_txt.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# PIPELINE ORCHESTRATOR
# =============================================================================

class NewsPipeline:
    """
    Runs the full news pipeline:
      0. Cleanup   — delete all landing files from the previous run
      1. Fetch     — pull articles from NewsAPI per topic search query
      2. Build     — merge & curate into a single master list
      3. Scrape    — download full article text
      4. Summarise — Gemini summary
      5. Classify  — second Gemini call; article only counts if it lands in
                     one of the valid categories defined in config.yaml
    """

    _LANDING_PATTERNS = [
        "news_*.json",
        "master_news.json",
        "pipeline_progress.json",
        "processed_articles.json",
        "summary_*.txt",
    ]

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg        = cfg
        # Ensure output directory exists.
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)

        self.fetcher    = ArticleFetcher(cfg)
        self.builder    = MasterBuilder(cfg)
        self.scraper    = ArticleScraper(cfg)
        self.summarizer = ArticleSummarizer(cfg)
        self.classifier = ArticleClassifier(cfg)
        self.tracker    = ProgressTracker(cfg)
        self.log_writer = CategoryLogWriter(cfg)

    # ------------------------------------------------------------------
    def _step_cleanup(self) -> None:
        log.info("━━━━  STEP 0: Cleanup  ━━━━")
        removed, failed = 0, 0
        for pattern in self._LANDING_PATTERNS:
            for path in self.cfg.output_dir.glob(pattern):
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    log_error(f"cleanup:{path.name}", exc)
                    failed += 1

        if failed:
            log.warning("  Cleanup: %d files removed, %d could not be deleted", removed, failed)
        else:
            log.info("  Cleanup: %d files removed.", removed)

        self.tracker.results = {}

    def _step_fetch(self) -> bool:
        log.info("━━━━  STEP 1: Fetch  ━━━━")
        try:
            saved = self.fetcher.fetch_all()
            return bool(saved)
        except Exception as exc:
            log_error("step:fetch", exc)
            return False

    def _step_build(self) -> bool:
        log.info("━━━━  STEP 2: Build master list  ━━━━")
        try:
            self.builder.build()
            return True
        except Exception as exc:
            log_error("step:build", exc)
            return False

    def _step_process(self) -> bool:
        log.info(
            "━━━━  STEPS 3–5: Scrape, Summarise & Classify  (goal: %d)  ━━━━",
            self.cfg.success_goal,
        )
        master = self.builder.load()
        articles: list[dict] = master.get("articles", [])
        total = len(articles)

        if not total:
            log.error("[step:process] Master list is empty — nothing to process.")
            return False

        log.info("Loaded %d candidates from master list.", total)
        log.info("Active categories: %s", ", ".join(self.cfg.valid_categories))

        for idx, article in enumerate(articles):
            if idx in self.tracker.processed_indices:
                continue
            if self.tracker.success_count >= self.cfg.success_goal:
                log.info("Success goal reached (%d). Stopping.", self.cfg.success_goal)
                break

            url    = (article.get("url") or "").strip()
            title  = article.get("title") or "Unknown"
            source = article.get("source", {}).get("name") or "Unknown"
            author = article.get("author") or "Unknown"
            log.info("[%d/%d] %s", idx + 1, total, title[:70])

            if author == "Unknown":
                log_warning(f"author:{idx}", "No author — skipping")
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, scrape_error="No author",
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            # ── Step 3: Scrape ──────────────────────────────────────────
            ok, content, err = self.scraper.scrape(article)
            if not ok:
                log_warning(f"scrape:{idx}", err)
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, scrape_error=err,
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            log.info("  ✓ Scraped %d chars", len(content))

            # ── Step 4: Summarise ───────────────────────────────────────
            ok, summary, err = self.summarizer.summarize(content)
            if not ok:
                log_warning(f"summarize:{idx}", err)
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, content=content, summary_error=err,
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            log.info("  ✓ Summarised %d chars", len(summary))

            # ── Step 5: Classify ────────────────────────────────────────
            ok, label, err = self.classifier.classify(title, summary)
            if not ok:
                log_warning(f"classify:{idx}", err)
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, content=content, summary=summary, classify_error=err,
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            # Validate label — must exactly match one of the configured categories.
            if label not in self.cfg.valid_categories:
                reason = (
                    f"Classified as '{label}' — not a valid category "
                    f"(must be one of: {', '.join(self.cfg.valid_categories)})"
                )
                log_warning(f"classify:{idx}", reason)
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, content=content, summary=summary, classify_error=reason,
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            log.info(
                "  ✓ Category: %s  [%d/%d successes]",
                label, self.tracker.success_count + 1, self.cfg.success_goal,
            )
            self.tracker.record(ArticleResult(
                index=idx, url=url, title=title, source=source, author=author,
                success=True, content=content, summary=summary, category=label,
            ))
            time.sleep(self.cfg.inter_request_delay)

        self._save_final_output()
        log.info(
            "Processing done — %d/%d successes from %d candidates.",
            self.tracker.success_count, self.cfg.success_goal, total,
        )
        return self.tracker.success_count >= self.cfg.success_goal

    def _save_final_output(self) -> None:
        successes = self.tracker.successes
        output = {
            "status": "ok",
            "total_processed": len(self.tracker.results),
            "total_successful": len(successes),
            "success_goal": self.cfg.success_goal,
            "completed_at": datetime.now().isoformat(),
            "articles": [r.to_dict() for r in successes],
        }
        self.cfg.results_file.write_text(json.dumps(output, indent=2), encoding="utf-8")
        log.info("Results written to %s", self.cfg.results_file)

        for i, result in enumerate(successes, start=1):
            txt_path = self.cfg.output_dir / f"summary_{i:02d}.txt"
            lines = [
                result.title,
                "=" * 60,
                "",
                result.summary or "",
                "",
                "=" * 60,
                f"Author:   {result.author}",
                f"Source:   {result.source}",
                f"URL:      {result.url}",
                f"Category: {result.category or 'Unknown'}",
            ]
            txt_path.write_text("\n".join(lines), encoding="utf-8")

        self.log_writer.record_run(successes)

    # ------------------------------------------------------------------
    def run(self) -> bool:
        log.info("══════  NEWS PIPELINE START  ══════")
        log.info(
            "Goal: %d successes from %d candidates",
            self.cfg.success_goal,
            self.cfg.candidate_count,
        )
        log.info("Topics:     %s", ", ".join(self.cfg.topics))
        log.info("Categories: %s", ", ".join(self.cfg.valid_categories))
        log.info("Output dir: %s", self.cfg.output_dir.resolve())

        self._step_cleanup()

        if not self._step_fetch():
            log.error("Pipeline halted at Step 1 (Fetch).")
            return False

        if not self._step_build():
            log.error("Pipeline halted at Step 2 (Build).")
            return False

        result = self._step_process()

        log.info("══════  NEWS PIPELINE END  ══════")
        return result


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    cfg = PipelineConfig.from_yaml("config.yaml")
    pipeline = NewsPipeline(cfg)
    ok = pipeline.run()
    raise SystemExit(0 if ok else 1)