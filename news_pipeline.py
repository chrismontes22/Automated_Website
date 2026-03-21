"""
News Pipeline
=============
Fetches, curates, scrapes, and summarizes financial news articles.
Designed for resilience: retries failed steps, saves progress after each
article, and resumes cleanly from a previous interrupted run.

After each successful summary a second Gemini call classifies the article
into one of four valid categories.  Articles landing in "Other" or returning
an unexpected string do NOT count toward the success goal.

Persistent logs (never deleted):
  category_log.json  – machine-readable record of every run
  category_log.txt   – human-readable; cumulative totals at top, runs below
"""

from __future__ import annotations

import glob
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import trafilatura
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

# Exact strings the classifier must return to pass.
VALID_CATEGORIES: list[str] = [
    "Artificial Intelligence",
    "Tech Business & Markets",
    "Cybersecurity",
    "Laptops & Cell Phones",
]

# The fifth option — articles labelled this way (or anything unrecognised) fail.
CATEGORY_OTHER = "Other"

# All recognised strings (used for prompt injection).
ALL_CATEGORIES: list[str] = VALID_CATEGORIES + [CATEGORY_OTHER]


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class PipelineConfig:
    # NewsAPI
    topics: list[str]           = field(default_factory=lambda: ["stocks", "economy", "prices", "money", "finance"])
    domains: str                = ""
    page_size: int              = 100
    sort_by: str                = "popularity"
    hours_back_from: int        = 37
    hours_back_to: int          = 25

    # Curation
    candidate_count: int        = 50
    source_limit: int           = 1

    # Scraping
    min_content_length: int     = 1_000
    chunk_size: int             = 150
    scrape_timeout: int         = 30

    # Summarisation
    gemini_model: str           = "gemini-3-flash-preview"
    max_output_tokens: int      = 2_000
    temperature: float          = 0

    # Classification
    classifier_model: str       = "gemini-3.1-flash-lite-preview"
    classifier_delay: float     = 4.0   # seconds to wait before each classify call (RPM guard)

    # Pipeline
    success_goal: int           = 20
    retry_attempts: int         = 3
    retry_backoff: float        = 2.0   # seconds; doubles on each retry
    inter_request_delay: float  = 1.0   # polite pause between scrapes

    # Paths
    output_dir: Path            = Path(".")

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
# RETRY DECORATOR
# =============================================================================

def with_retry(attempts: int = 3, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    """
    Decorator: re-runs the wrapped function up to `attempts` times on failure,
    waiting backoff * 2^n seconds between tries.
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
# ARTICLE FETCHER
# =============================================================================

class ArticleFetcher:
    """Fetches raw news articles from NewsAPI for each configured topic."""

    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("NEWS_API_KEY")

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

    @with_retry(attempts=3, backoff=2.0, exceptions=(requests.RequestException,))
    def _fetch_one(self, topic: str, from_dt: str, to_dt: str) -> dict:
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
        """Fetch every topic and persist each response to its own JSON file."""
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

    Strategy: round-robin across topics so no single topic dominates,
    with a hard cap of `source_limit` articles per publisher.
    Deduplication covers URL, title, and author to block reposts across sources.
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
                log_warning(f"build:load:{path.name}", f"Skipping unreadable file — {type(exc).__name__}: {exc}")

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
        dist_lines = [f"  {s}: {c}" for s, c in
                      sorted(source_counts.items(), key=lambda x: -x[1])]
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

    def _deduplicate(self, text: str) -> str:
        """Remove repeated content blocks (common with scrapers)."""
        cs = self.cfg.chunk_size
        if len(text) <= cs * 2:
            return text
        first_chunk = text[:cs]
        dup_idx = text.find(first_chunk, cs)
        return text[:dup_idx].strip() if dup_idx != -1 else text

    @with_retry(attempts=2, backoff=3.0, exceptions=(Exception,))
    def _download_and_extract(self, url: str) -> str:
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

SUMMARY_PROMPT = """\
You are a financial news analyst. Summarise the article below for a general
audience. Be concise and plain-English. Structure your response as:

**One-sentence headline summary**

**Key points** (3–6 bullet points)

**Why it matters** (1–2 sentences)

Article:
{text}
"""

class ArticleSummarizer:
    """Summarises article text using Google Gemini."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None

        if not GEMINI_AVAILABLE:
            log_warning("summarizer:init", "google-genai is not installed — summarisation unavailable")
            return
        if not self._api_key:
            log_warning("summarizer:init", "GEMINI_KEY is not set — summarisation unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)

    def _is_ready(self) -> bool:
        return self._client is not None

    @with_retry(attempts=3, backoff=5.0, exceptions=(Exception,))
    def _call_api(self, text: str) -> str:
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

CLASSIFIER_PROMPT = """\
You are a news classification agent. Your only job is to read the article \
information below and output exactly one of these category labels — nothing \
else, no explanation, no punctuation, no extra words:

  Artificial Intelligence
  Tech Business & Markets
  Cybersecurity
  Laptops & Cell Phones
  Other

Use "Other" only if the article does not clearly fit any of the four \
specific categories above.

Article title: {title}

Article summary:
{summary}
"""


class ArticleClassifier:
    """
    Classifies a summarised article into one of VALID_CATEGORIES using Gemini.

    Rules:
      • Temperature is always 0.
      • A configurable delay is inserted before each API call to stay within
        the model's requests-per-minute quota.
      • Returns the raw label string; validation against VALID_CATEGORIES is
        done by the caller.
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None

        if not GEMINI_AVAILABLE:
            log_warning("classifier:init", "google-genai is not installed — classification unavailable")
            return
        if not self._api_key:
            log_warning("classifier:init", "GEMINI_KEY is not set — classification unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)

    def _is_ready(self) -> bool:
        return self._client is not None

    @with_retry(attempts=3, backoff=6.0, exceptions=(Exception,))
    def _call_api(self, title: str, summary: str) -> str:
        prompt = CLASSIFIER_PROMPT.format(title=title, summary=summary)
        response = self._client.models.generate_content(
            model=self.cfg.classifier_model,
            contents=prompt,
            config={
                "max_output_tokens": 20,   # category label only — very short
                "temperature": 0,          # always deterministic
            },
        )
        label = (response.text or "").strip()
        return label

    def classify(
        self, title: str, summary: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Returns (api_ok, label_or_None, error_or_None).

        api_ok is True even when the label is "Other" or unrecognised —
        it only reflects whether the API call itself succeeded.
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
    category: Optional[str]      = None   # set only on full success
    content: Optional[str]       = None
    summary: Optional[str]       = None
    scrape_error: Optional[str]  = None
    summary_error: Optional[str] = None
    classify_error: Optional[str] = None
    processed_at: str            = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> ArticleResult:
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
            log_warning("tracker:load", f"Could not load progress file — starting fresh. {type(exc).__name__}: {exc}")
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
    category_log.txt   — human-readable; never deleted; rewritten each run
                         with cumulative totals at the top
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    def _load_history(self) -> list[dict]:
        """Load existing run records from the JSON log, or return empty list."""
        if not self.cfg.category_log_json.exists():
            return []
        try:
            return json.loads(self.cfg.category_log_json.read_text(encoding="utf-8"))
        except Exception as exc:
            log_warning("category_log:load", f"Could not read log JSON — starting fresh. {exc}")
            return []

    # ------------------------------------------------------------------
    def record_run(self, successes: list[ArticleResult]) -> None:
        """Append this run's category counts and rewrite both log files."""
        # Build per-category counts for this run
        counts: dict[str, int] = {cat: 0 for cat in VALID_CATEGORIES}
        for r in successes:
            if r.category in counts:
                counts[r.category] += 1

        run_entry = {
            "run_at": datetime.now().isoformat(),
            "total_successes": len(successes),
            "category_counts": counts,
        }

        history = self._load_history()
        history.append(run_entry)

        # Persist JSON
        self.cfg.category_log_json.write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )

        # Rewrite human-readable txt
        self._write_txt(history)

        log.info(
            "Category log updated: %d total runs on record.",
            len(history),
        )

    # ------------------------------------------------------------------
    def _write_txt(self, history: list[dict]) -> None:
        """Write the complete txt log: cumulative totals first, then per-run."""
        # --- Cumulative totals ---
        cumulative: dict[str, int] = {cat: 0 for cat in VALID_CATEGORIES}
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
        for cat in VALID_CATEGORIES:
            lines.append(f"  {cat:<28}  {cumulative[cat]:>4}")
        lines += [
            divider_thin,
            f"  {'TOTAL (valid categories)':<28}  {cum_total:>4}",
            divider_thick,
            "",
        ]

        # --- Per-run entries (newest first) ---
        for run in reversed(history):
            run_counts = run.get("category_counts", {})
            run_total  = run.get("total_successes", 0)
            run_at     = run.get("run_at", "unknown")
            lines += [
                f"Run  {run_at}   ({run_total} successes)",
                divider_thin,
            ]
            for cat in VALID_CATEGORIES:
                lines.append(f"  {cat:<28}  {run_counts.get(cat, 0):>4}")
            lines.append("")

        self.cfg.category_log_txt.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# PIPELINE ORCHESTRATOR
# =============================================================================

class NewsPipeline:
    """
    Runs the full news pipeline:
      0. Cleanup  — delete all landing files from the previous run
      1. Fetch    — pull articles from NewsAPI per topic
      2. Build    — merge & curate into a single master list
      3. Scrape   — download full article text
      4. Summarise — Gemini summary
      5. Classify  — second Gemini call; article only counts if it lands in
                      one of the four valid categories
    """

    # Glob patterns for files produced by the pipeline (NOT the persistent logs)
    _LANDING_PATTERNS = [
        "news_*.json",
        "master_news.json",
        "pipeline_progress.json",
        "processed_articles.json",
        "summary_*.txt",
    ]

    def __init__(self, cfg: Optional[PipelineConfig] = None) -> None:
        self.cfg        = cfg or PipelineConfig()
        self.fetcher    = ArticleFetcher(self.cfg)
        self.builder    = MasterBuilder(self.cfg)
        self.scraper    = ArticleScraper(self.cfg)
        self.summarizer = ArticleSummarizer(self.cfg)
        self.classifier = ArticleClassifier(self.cfg)
        self.tracker    = ProgressTracker(self.cfg)
        self.log_writer = CategoryLogWriter(self.cfg)

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
                    success=False, content=content, summary=summary,
                    classify_error=err,
                ))
                time.sleep(self.cfg.inter_request_delay)
                continue

            # Validate label — must exactly match one of the four valid categories
            if label not in VALID_CATEGORIES:
                reason = (
                    f"Classified as '{label}' — not a valid category "
                    f"(must be one of: {', '.join(VALID_CATEGORIES)})"
                )
                log_warning(f"classify:{idx}", reason)
                self.tracker.record(ArticleResult(
                    index=idx, url=url, title=title, source=source, author=author,
                    success=False, content=content, summary=summary,
                    classify_error=reason,
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
                f"Category: {result.category or 'Unknown'}",
                f"Author:   {result.author}",
                f"Source:   {result.source}",
                f"URL:      {result.url}",
            ]
            txt_path.write_text("\n".join(lines), encoding="utf-8")

        # Update the persistent category log
        self.log_writer.record_run(successes)

    # ------------------------------------------------------------------
    def run(self) -> bool:
        log.info("══════  NEWS PIPELINE START  ══════")
        log.info(
            "Goal: %d successes from %d candidates | Topics: %s",
            self.cfg.success_goal,
            self.cfg.candidate_count,
            ", ".join(self.cfg.topics),
        )

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
    cfg = PipelineConfig(
        topics          = ["Technology", "Artificial intelligence", "Laptops", "Cybersecurity", "Technology Market"],
        candidate_count = 50,
        success_goal    = 20,
        source_limit    = 1,
        gemini_model    = "gemini-3.1-flash-lite-preview",
        classifier_model = "gemini-3.1-flash-lite-preview",
        classifier_delay = 4.0,   # seconds between classify calls — tune to your RPM quota
        output_dir      = Path("."),
    )

    pipeline = NewsPipeline(cfg)
    ok = pipeline.run()
    raise SystemExit(0 if ok else 1)