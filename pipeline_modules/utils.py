"""
pipeline_modules/utils.py
=========================
Shared utilities used across the pipeline:
  - Logging helpers (log_error, log_warning)
  - Retry decorator (with_retry)
  - Classification constant (CATEGORY_OTHER)
  - AI prompt strings and builders (SUMMARY_PROMPT, build_classifier_prompt)
"""

from __future__ import annotations

import logging
import time
from functools import wraps

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

### Key Points
- Exactly 3 to 6 bullet points
- Each bullet should be short and information-dense
- Include concrete facts, names, numbers, products, companies, dates, or locations when present
- Prefer specific nouns and verbs over vague language

### Why it Matters
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
