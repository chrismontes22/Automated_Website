"""
build_site_data.py
==================
Run this AFTER news_pipeline.py.  Reads processed_articles.json (the pipeline's
per-run output) and appends any new articles to articles.json (the permanent
site data file that the website reads).

Default file paths are derived from output_dir in config.yaml.  They can be
overridden with CLI flags for non-standard setups.

Deduplication is by URL — running it multiple times is safe.

Usage:
    python build_site_data.py
    python build_site_data.py --config path/to/config.yaml
    python build_site_data.py --pipeline-output path/to/processed_articles.json
    python build_site_data.py --site-data path/to/articles.json
"""
from __future__ import annotations

import argparse
import json
import logging
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
    Read output_dir from config.yaml.  Returns Path(".") if the file is absent
    or the key is missing, so the script still works without a config when paths
    are supplied explicitly via CLI flags.
    """
    p = Path(config_path)
    if not p.exists():
        log.warning(
            "config.yaml not found at %s — using current directory for default paths.",
            p.resolve(),
        )
        return Path(".")
    try:
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        output_dir = Path(raw["paths"]["output_dir"])
        log.info("output_dir from config: %s", output_dir.resolve())
        return output_dir
    except Exception as exc:
        log.warning(
            "Could not read output_dir from config (%s) — using current directory.",
            exc,
        )
        return Path(".")


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
    Returns (merged_list, count_added).
    """
    existing_urls = {a.get("url", "").strip() for a in existing}
    to_add = [slim(a) for a in new_articles if a.get("url", "").strip() not in existing_urls]
    merged = to_add + existing
    return merged, len(to_add)


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
    parser.add_argument(
        "--site-data",
        default=str(output_dir / "articles.json"),
        help="Path to the persistent site data file (default: <output_dir>/articles.json)",
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