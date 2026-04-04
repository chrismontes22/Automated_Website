"""
pipeline_modules/builder.py
===========================
MasterBuilder — merges per-topic JSON files into one curated, deduplicated
master list.

Strategy: round-robin across topics so no single topic dominates, with a
hard cap of `source_limit` articles per publisher.  Deduplication covers
URL, title, and author to block reposts across sources.
"""

from __future__ import annotations

import json
from datetime import datetime

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import log, log_warning


class MasterBuilder:
    """
    Merges per-topic JSON files into one curated, deduplicated master list.
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
