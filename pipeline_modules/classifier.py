"""
pipeline_modules/classifier.py
===============================
ArticleClassifier — classifies a summarised article into one of the
valid_categories defined in config.yaml using a second Gemini call.

Rules:
  • Temperature is always 0 (deterministic).
  • A configurable delay is inserted before each API call to respect RPM limits.
  • The accepted category list is driven entirely by config.yaml.
  • Returns the raw label string; validation against valid_categories is
    performed by the pipeline orchestrator.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from dotenv import load_dotenv

from pipeline_modules.config import PipelineConfig
from pipeline_modules.utils import log, log_warning, with_retry, build_classifier_prompt

load_dotenv()

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class ArticleClassifier:
    """
    Classifies a summarised article into one of cfg.valid_categories using Gemini.
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._api_key = os.getenv("GEMINI_KEY")
        self._client = None
        self._call_api = None

        if not GEMINI_AVAILABLE:
            log_warning("classifier:init", "google-genai is not installed — classification unavailable")
            return
        if not self._api_key:
            log_warning("classifier:init", "GEMINI_KEY is not set — classification unavailable")
            return

        self._client = genai.Client(api_key=self._api_key)
        self._call_api = with_retry(
            attempts=cfg.retry_attempts,
            backoff=cfg.retry_backoff,
            exceptions=(Exception,),
        )(self._call_api_impl)

    def _is_ready(self) -> bool:
        return self._client is not None and self._call_api is not None

    def _call_api_impl(self, title: str, summary: str) -> str:
        prompt = build_classifier_prompt(title, summary, self.cfg.valid_categories)
        response = self._client.models.generate_content(
            model=self.cfg.classifier_model,
            contents=prompt,
            config={
                "max_output_tokens": 20,
                "temperature": 0,   # always deterministic for classification
            },
        )
        return (response.text or "").strip()

    def classify(
        self, title: str, summary: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Returns (api_ok, label_or_None, error_or_None).

        api_ok is True even when the label is "Other" or unrecognised — it
        only reflects whether the API call itself succeeded.
        """
        if not self._is_ready():
            return False, None, "Gemini classifier not initialised (check GEMINI_KEY and google-genai install)"
        if not summary:
            return False, None, "Empty summary passed to classifier"

        log.info("  [classify] Waiting %.1fs before API call (RPM guard)…", self.cfg.classifier_delay)
        time.sleep(self.cfg.classifier_delay)

        try:
            label = self._call_api(title, summary)
            return True, label, None
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
