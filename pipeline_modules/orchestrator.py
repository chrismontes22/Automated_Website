"""
pipeline_modules/orchestrator.py
=================================
NewsPipeline — the top-level orchestrator that ties all pipeline steps together.

Steps:
  0. Cleanup   — delete all landing files from the previous run
  1. Fetch     — pull articles from NewsAPI per topic search query
  2. Build     — merge & curate into a single master list
  3. Scrape    — download full article text
  4. Summarise — Gemini summary
  5. Classify  — second Gemini call; article only counts if it lands in
                 one of the valid categories defined in config.yaml
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from pipeline_modules.builder import MasterBuilder
from pipeline_modules.classifier import ArticleClassifier
from pipeline_modules.config import PipelineConfig
from pipeline_modules.fetcher import ArticleFetcher
from pipeline_modules.log_writer import CategoryLogWriter
from pipeline_modules.scraper import ArticleScraper
from pipeline_modules.summarizer import ArticleSummarizer
from pipeline_modules.tracker import ArticleResult, ProgressTracker
from pipeline_modules.utils import log, log_error, log_warning


class NewsPipeline:
    """
    Runs the full news pipeline end-to-end, with resume support.
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
