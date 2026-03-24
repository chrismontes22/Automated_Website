"""
build_site_data.py
==================
Run this AFTER news_pipeline.py.  Reads processed_articles.json (the pipeline's
per-run output) and appends any new articles to articles.json (the permanent
site data file that the website reads).

articles.json is ALWAYS saved to the current directory (.) so index.html can
fetch it directly. processed_articles.json path comes from config.yaml.

Deduplication is by URL *and* normalized title — running it multiple times is safe.
Articles with matching URL OR matching title (case-insensitive, stripped) are skipped.

Usage:
    python build_site_data.py
    python build_site_data.py --config path/to/config.yaml
    python build_site_data.py --pipeline-output path/to/processed_articles.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_site_data")

# Fields carried into the website JSON (raw scraped content is dropped to keep the file small).
KEEP_FIELDS = {"title", "summary", "author", "source", "url", "category", "processed_at"}


# =============================================================================
# CONFIG HELPERS
# =============================================================================

def get_output_dir(config_path: str) -> Path:
    """
    Read output_dir from config.yaml. Returns Path("other") if the file is absent
    or the key is missing. This controls where processed_articles.json is read from.
    """
    p = Path(config_path)
    if not p.exists():
        log.warning(
            "config.yaml not found at %s — using 'other/' for pipeline output.",
            p.resolve(),
        )
        return Path("other")
    try:
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        output_dir = Path(raw["paths"]["output_dir"])
        log.info("output_dir from config: %s", output_dir.resolve())
        return output_dir
    except Exception as exc:
        log.warning(
            "Could not read output_dir from config (%s) — using 'other/'.",
            exc,
        )
        return Path("other")


# =============================================================================
# DEDUPLICATION HELPERS
# =============================================================================

def normalize_title(title: str | None) -> str:
    """
    Normalize a title for comparison: lowercase, strip whitespace,
    collapse multiple spaces, remove extra punctuation spacing.
    """
    if not title:
        return ""
    # Lowercase and strip
    t = title.lower().strip()
    # Collapse multiple whitespace
    t = re.sub(r"\s+", " ", t)
    # Optional: remove trailing punctuation that varies between sites
    t = re.sub(r"\s*[-–—|:]\s*$", "", t)
    return t


def make_dedup_key(article: dict) -> tuple[str, str]:
    """
    Create a deduplication key: (normalized_url, normalized_title).
    Both are stripped and normalized for reliable matching.
    """
    url = (article.get("url") or "").strip().lower()
    title = normalize_title(article.get("title"))
    return (url, title)


# =============================================================================
# CORE LOGIC
# =============================================================================

def load_pipeline_output(path: Path) -> list[dict]:
    """Load successful articles from the pipeline's processed_articles.json."""
    if not path.exists():
        raise FileNotFoundError(f"Pipeline output not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    log.info("Pipeline output: %d successful articles", len(articles))
    return articles


def load_site_data(path: Path) -> list[dict]:
    """Load existing articles.json, or return an empty list if it doesn't exist yet."""
    if not path.exists():
        log.info("No existing %s — will create a new one.", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    log.info("Existing %s: %d articles", path, len(data))
    return data


def slim(article: dict) -> dict:
    """Keep only website-relevant fields and normalise types."""
    out = {k: article.get(k, "") for k in KEEP_FIELDS}
    for k in out:
        if out[k] is None:
            out[k] = ""
    return out


def merge(existing: list[dict], new_articles: list[dict]) -> tuple[list[dict], int]:
    """
    Prepend new articles to the front (newest first).
    Deduplicates by URL *or* normalized title.
    Returns (merged_list, count_added).
    """
    # Build set of existing dedup keys: (url, title)
    existing_keys = {make_dedup_key(a) for a in existing}
    
    to_add = []
    skipped_by_url = 0
    skipped_by_title = 0
    
    for article in new_articles:
        key = make_dedup_key(article)
        url, title = key
        
        # Skip if URL matches
        if any(url == ek[0] for ek in existing_keys):
            skipped_by_url += 1
            continue
        # Skip if normalized title matches (but URL is different)
        if title and any(title == ek[1] for ek in existing_keys):
            skipped_by_title += 1
            continue
            
        to_add.append(slim(article))
    
    merged = to_add + existing
    added = len(to_add)
    
    log.debug(
        "Dedup stats: %d skipped by URL, %d skipped by title, %d added",
        skipped_by_url, skipped_by_title, added
    )
    
    return merged, added


def save_site_data(path: Path, articles: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(articles, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved %s → %d total articles", path, len(articles))


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:
    # ── First pass: read --config so we can derive sensible defaults ──
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default="config.yaml")
    pre_args, _ = pre_parser.parse_known_args()

    output_dir = get_output_dir(pre_args.config)

    # ── Full parser with config-aware defaults ────────────────────────
    parser = argparse.ArgumentParser(description="Merge pipeline output into articles.json")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--pipeline-output",
        default=str(output_dir / "processed_articles.json"),
        help="Path to the pipeline's per-run output (default: <output_dir>/processed_articles.json)",
    )
    # articles.json is ALWAYS in current directory (.) for index.html to fetch
    parser.add_argument(
        "--site-data",
        default="articles.json",
        help="Path to the persistent site data file (default: ./articles.json)",
    )
    args = parser.parse_args()

    pipeline_path = Path(args.pipeline_output)
    site_path     = Path(args.site_data)

    new_articles = load_pipeline_output(pipeline_path)
    existing     = load_site_data(site_path)
    merged, added = merge(existing, new_articles)
    save_site_data(site_path, merged)

    log.info(
        "Done — %d new article(s) added, %d duplicate(s) skipped.",
        added,
        len(new_articles) - added,
    )


if __name__ == "__main__":
    main()