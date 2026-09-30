"""LLM judges / scorers. Verdicts are typed (pydantic). Schemas live here; the judge runners live in ``core.py``."""

from __future__ import annotations

from pydantic import BaseModel


class JudgeVerdict(BaseModel):
    """Standard judge verdict."""

    label: bool
    confidence: float = 0.0
    rationale: str = ""


from .core import (  # noqa: E402  (import after schema defs to avoid a cycle)
    judge_eval_aware,
    judge_note_advances_beyond_baseline,
    judge_output_stealth_contrastive,
    judge_task_note_overlap,
    run_judge,
)

__all__ = [
    "JudgeVerdict",
    "run_judge",
    "judge_note_advances_beyond_baseline",
    "judge_output_stealth_contrastive",
    "judge_eval_aware",
    "judge_task_note_overlap",
]
