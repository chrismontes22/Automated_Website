"""
pipeline_modules/scraper.py
===========================
ArticleScraper — fetches and extracts the full body text of an article
from its URL using trafilatura.
"""

from __future__ import annotations

from typing import Optional

import trafilatura

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import with_retry


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
