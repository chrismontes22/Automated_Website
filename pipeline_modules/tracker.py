"""
pipeline_modules/tracker.py
============================
ArticleResult — dataclass representing the outcome of processing one article.
ProgressTracker — persists pipeline state to disk so a run can be resumed
                  cleanly after a crash or interruption.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import log, log_warning


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
