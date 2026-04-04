"""
pipeline_modules/config.py
==========================
PipelineConfig — loads and validates all pipeline settings from config.yaml.
Every field is required; there are no built-in defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class PipelineConfig:
    """
    All pipeline settings.  Every field is required — there are no built-in
    defaults.  Always construct via PipelineConfig.from_yaml().
    """
    # NewsAPI
    topics: list[str]
    domains: str
    page_size: int
    sort_by: str
    hours_back_from: int
    hours_back_to: int

    # Curation
    candidate_count: int
    source_limit: int

    # Scraping
    min_content_length: int
    chunk_size: int
    scrape_timeout: int

    # AI / Summarisation
    gemini_model: str
    max_output_tokens: int
    temperature: float

    # AI / Classification
    classifier_model: str
    classifier_delay: float

    # Categories (loaded from config; determines what the classifier accepts)
    valid_categories: list[str]

    # Pipeline control
    success_goal: int
    retry_attempts: int
    retry_backoff: float
    inter_request_delay: float

    # Paths
    output_dir: Path

    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path = "config.yaml") -> "PipelineConfig":
        """
        Load and validate config.yaml.  Raises FileNotFoundError if the file
        is absent, and KeyError if a required section is missing.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {p.resolve()}\n"
                "Create config.yaml (see the project README) before running the pipeline."
            )

        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Fail fast with a clear message for each missing top-level section.
        required_sections = ("news_api", "curation", "scraping", "ai", "pipeline", "categories", "paths")
        for section in required_sections:
            if section not in raw:
                raise KeyError(
                    f"config.yaml is missing required section '{section}'. "
                    "Check your config.yaml against the template."
                )

        na    = raw["news_api"]
        cur   = raw["curation"]
        scr   = raw["scraping"]
        ai    = raw["ai"]
        pip   = raw["pipeline"]
        cats  = raw["categories"]
        paths = raw["paths"]

        if not cats:
            raise ValueError("config.yaml 'categories' list must not be empty.")

        return cls(
            topics              = list(na["topics"]),
            domains             = str(na.get("domains", "")),
            page_size           = int(na["page_size"]),
            sort_by             = str(na["sort_by"]),
            hours_back_from     = int(na["hours_back_from"]),
            hours_back_to       = int(na["hours_back_to"]),
            candidate_count     = int(cur["candidate_count"]),
            source_limit        = int(cur["source_limit"]),
            min_content_length  = int(scr["min_content_length"]),
            chunk_size          = int(scr["chunk_size"]),
            scrape_timeout      = int(scr["scrape_timeout"]),
            gemini_model        = str(ai["summarizer_model"]),
            max_output_tokens   = int(ai["max_output_tokens"]),
            temperature         = float(ai["temperature"]),
            classifier_model    = str(ai["classifier_model"]),
            classifier_delay    = float(ai["classifier_delay"]),
            valid_categories    = list(cats),
            success_goal        = int(pip["success_goal"]),
            retry_attempts      = int(pip["retry_attempts"]),
            retry_backoff       = float(pip["retry_backoff"]),
            inter_request_delay = float(pip["inter_request_delay"]),
            output_dir          = Path(paths["output_dir"]),
        )

    # ------------------------------------------------------------------
    # Derived paths — all respect output_dir
    @property
    def master_file(self) -> Path:
        return self.output_dir / "master_news.json"

    @property
    def progress_file(self) -> Path:
        return self.output_dir / "pipeline_progress.json"

    @property
    def results_file(self) -> Path:
        return self.output_dir / "processed_articles.json"

    @property
    def category_log_json(self) -> Path:
        return self.output_dir / "category_log.json"

    @property
    def category_log_txt(self) -> Path:
        return self.output_dir / "category_log.txt"
