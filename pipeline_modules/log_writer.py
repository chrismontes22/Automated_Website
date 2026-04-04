"""
pipeline_modules/log_writer.py
===============================
CategoryLogWriter — maintains a persistent, cumulative log of per-run and
all-time category counts.

  category_log.json  — machine-readable; never deleted
  category_log.txt   — human-readable; never deleted; cumulative totals at top
"""

from __future__ import annotations

from datetime import datetime

from pipeline_modules.config import PipelineConfig
from pipeline_modules.tracker import ArticleResult
from pipeline_modules.utils import log, log_warning


class CategoryLogWriter:
    """
    Maintains a persistent log of per-run and cumulative category counts.
    """

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg

    def _load_history(self) -> list[dict]:
        if not self.cfg.category_log_json.exists():
            return []
        try:
            import json
            return json.loads(self.cfg.category_log_json.read_text(encoding="utf-8"))
        except Exception as exc:
            log_warning("category_log:load", f"Could not read log JSON — starting fresh. {exc}")
            return []

    def record_run(self, successes: list[ArticleResult]) -> None:
        """Append this run's category counts and rewrite both log files."""
        import json

        counts: dict[str, int] = {cat: 0 for cat in self.cfg.valid_categories}
        for r in successes:
            if r.category in counts:
                counts[r.category] += 1

        run_entry = {
            "run_at": datetime.now().isoformat(),
            "total_successes": len(successes),
            "categories": self.cfg.valid_categories,   # stored so txt can handle config changes
            "category_counts": counts,
        }

        history = self._load_history()
        history.append(run_entry)

        self.cfg.category_log_json.write_text(json.dumps(history, indent=2), encoding="utf-8")
        self._write_txt(history)

        log.info("Category log updated: %d total runs on record.", len(history))

    def _write_txt(self, history: list[dict]) -> None:
        """Write the complete txt log: cumulative totals first, then per-run (newest first)."""
        # Collect all unique categories across all runs (preserving first-seen order).
        seen: set[str] = set()
        all_cats: list[str] = []
        for run in history:
            for cat in run.get("categories", list(run.get("category_counts", {}).keys())):
                if cat not in seen:
                    all_cats.append(cat)
                    seen.add(cat)

        # Cumulative totals across all runs.
        cumulative: dict[str, int] = {cat: 0 for cat in all_cats}
        for run in history:
            for cat, n in run.get("category_counts", {}).items():
                if cat in cumulative:
                    cumulative[cat] += n

        cum_total = sum(cumulative.values())
        lines: list[str] = []

        divider_thick = "═" * 62
        divider_thin  = "─" * 62

        lines += [
            divider_thick,
            "  CUMULATIVE CATEGORY TOTALS  (all runs)",
            divider_thick,
        ]
        for cat in all_cats:
            lines.append(f"  {cat:<32}  {cumulative[cat]:>4}")
        lines += [
            divider_thin,
            f"  {'TOTAL (valid categories)':<32}  {cum_total:>4}",
            divider_thick,
            "",
        ]

        for run in reversed(history):
            run_counts = run.get("category_counts", {})
            run_cats   = run.get("categories", list(run_counts.keys()))
            run_total  = run.get("total_successes", 0)
            run_at     = run.get("run_at", "unknown")
            lines += [
                f"Run  {run_at}   ({run_total} successes)",
                divider_thin,
            ]
            for cat in run_cats:
                lines.append(f"  {cat:<32}  {run_counts.get(cat, 0):>4}")
            lines.append("")

        self.cfg.category_log_txt.write_text("\n".join(lines), encoding="utf-8")
