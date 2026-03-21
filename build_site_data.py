"""
build_site_data.py
==================
Run this AFTER pipeline.py.  It reads processed_articles.json (the pipeline's
per-run output) and appends any new articles to articles.json (the permanent
site data file that the website reads).

Deduplication is by URL — running it multiple times is safe.

Usage:
    python build_site_data.py
    python build_site_data.py --pipeline-output path/to/processed_articles.json
    python build_site_data.py --site-data    path/to/articles.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_site_data")


# Fields we carry into the website JSON (drop raw scraped content to keep file small)
KEEP_FIELDS = {"title", "summary", "author", "source", "url", "category", "processed_at"}


def load_pipeline_output(path: Path) -> list[dict]:
    """Load successful articles from the pipeline's processed_articles.json."""
    if not path.exists():
        raise FileNotFoundError(f"Pipeline output not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    log.info("Pipeline output: %d successful articles", len(articles))
    return articles


def load_site_data(path: Path) -> list[dict]:
    """Load existing articles.json, or return empty list if it doesn't exist yet."""
    if not path.exists():
        log.info("No existing articles.json — will create a new one.")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    log.info("Existing articles.json: %d articles", len(data))
    return data


def slim(article: dict) -> dict:
    """Keep only website-relevant fields and normalise types."""
    out = {k: article.get(k, "") for k in KEEP_FIELDS}
    # Ensure strings are clean
    for k in out:
        if out[k] is None:
            out[k] = ""
    return out


def merge(existing: list[dict], new_articles: list[dict]) -> tuple[list[dict], int]:
    """
    Prepend new articles to the front (newest first).
    Returns (merged_list, count_added).
    """
    existing_urls = {a.get("url", "").strip() for a in existing}
    to_add = [slim(a) for a in new_articles if a.get("url", "").strip() not in existing_urls]
    merged = to_add + existing          # newest at the front
    return merged, len(to_add)


def save_site_data(path: Path, articles: list[dict]) -> None:
    path.write_text(json.dumps(articles, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved articles.json → %d total articles", len(articles))


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge pipeline output into articles.json")
    parser.add_argument(
        "--pipeline-output",
        default="processed_articles.json",
        help="Path to pipeline's per-run output (default: processed_articles.json)",
    )
    parser.add_argument(
        "--site-data",
        default="articles.json",
        help="Path to the persistent site data file (default: articles.json)",
    )
    args = parser.parse_args()

    pipeline_path = Path(args.pipeline_output)
    site_path     = Path(args.site_data)

    new_articles = load_pipeline_output(pipeline_path)
    existing     = load_site_data(site_path)
    merged, added = merge(existing, new_articles)

    save_site_data(site_path, merged)
    log.info("Done — %d new article(s) added, %d duplicate(s) skipped.", added, len(new_articles) - added)


if __name__ == "__main__":
    main()
