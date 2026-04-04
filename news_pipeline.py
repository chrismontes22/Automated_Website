"""
news_pipeline.py
================
Entry point. Loads config and runs the pipeline.

All pipeline logic lives in the pipeline_modules/ package:
  config.py       — PipelineConfig
  fetcher.py      — ArticleFetcher
  builder.py      — MasterBuilder
  scraper.py      — ArticleScraper
  summarizer.py   — ArticleSummarizer
  classifier.py   — ArticleClassifier
  tracker.py      — ArticleResult, ProgressTracker
  log_writer.py   — CategoryLogWriter
  orchestrator.py — NewsPipeline
  utils.py        — logging helpers, retry decorator, constants, prompts
"""

from pipeline_modules import NewsPipeline, PipelineConfig

if __name__ == "__main__":
    cfg = PipelineConfig.from_yaml("config.yaml")
    pipeline = NewsPipeline(cfg)
    ok = pipeline.run()
    raise SystemExit(0 if ok else 1)
