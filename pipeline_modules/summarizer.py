"""
pipeline_modules/summarizer.py
==============================
ArticleSummarizer — summarises article text using Google Gemini.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import log_warning, with_retry, SUMMARY_PROMPT

load_dotenv()

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class ArticleSummarizer:
    """Summarises article text using Google Gemini."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None
        self._call_api = None

        if not GEMINI_AVAILABLE:
            log_warning("summarizer:init", "google-genai is not installed — summarisation unavailable")
            return
        if not self._api_key:
            log_warning("summarizer:init", "GEMINI_KEY is not set — summarisation unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)
        self._call_api = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(Exception,),
        )(self._call_api_impl)

    def _is_ready(self) -> bool:
        return self._client is not None and self._call_api is not None

    def _call_api_impl(self, text: str) -> str:
        response = self._client.models.generate_content(
            model=self.cfg.gemini_model,
            contents=SUMMARY_PROMPT.format(text=text),
            config={
                "max_output_tokens": self.cfg.max_output_tokens,
                "temperature": self.cfg.temperature,
            },
        )
        summary = (response.text or "").strip()
        if len(summary) < 50:
            raise ValueError(f"Summary suspiciously short ({len(summary)} chars) — possible API issue")
        return summary

    def summarize(self, text: str) -> tuple[bool, Optional[str], Optional[str]]:
        """Returns (success, summary_or_None, error_or_None)."""
        if not self._is_ready():
            return False, None, "Gemini client not initialised (check GEMINI_KEY and google-genai install)"
        if not text or len(text) < 100:
            return False, None, f"Input text too short to summarise ({len(text)} chars)"

        try:
            summary = self._call_api(text)
            return True, summary, None
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
