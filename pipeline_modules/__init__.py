"""
pipeline_modules
================
Public re-exports for the news pipeline package.

Importing directly from sub-modules is also fine — this file exists purely
for convenience so callers can write:

    from pipeline_modules import NewsPipeline, PipelineConfig
"""

from pipeline_modules.config import PipelineConfig
from pipeline_modules.fetcher import ArticleFetcher
from pipeline_modules.builder import MasterBuilder
from pipeline_modules.scraper import ArticleScraper
from pipeline_modules.summarizer import ArticleSummarizer
from pipeline_modules.classifier import ArticleClassifier
from pipeline_modules.tracker import ArticleResult, ProgressTracker
from pipeline_modules.log_writer import CategoryLogWriter
from pipeline_modules.orchestrator import NewsPipeline

__all__ = [
    "PipelineConfig",
    "ArticleFetcher",
    "MasterBuilder",
    "ArticleScraper",
    "ArticleSummarizer",
    "ArticleClassifier",
    "ArticleResult",
    "ProgressTracker",
    "CategoryLogWriter",
    "NewsPipeline",
]
