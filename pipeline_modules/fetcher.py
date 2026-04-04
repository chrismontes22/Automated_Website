"""
pipeline_modules/fetcher.py
===========================
ArticleFetcher — fetches raw news articles from NewsAPI for each configured
topic (search query) and saves each response to its own JSON file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import log, log_error, with_retry

load_dotenv()


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
