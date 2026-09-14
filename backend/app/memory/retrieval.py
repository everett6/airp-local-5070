"""
Turns recorded outcomes into calibration/accuracy statistics — this is what
"continuously learns from previous trades" means concretely in this system:
NOT online weight updates to an LLM, but a deterministic scorecard that (a)
future debate stages can retrieve as memory context, and (b) surfaces
systematic biases (e.g. this bull agent is chronically 15% too optimistic on
price targets) for prompt/config revision by a human maintainer.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.memory.interface import MemoryKind, MemoryRecord


@dataclass(frozen=True)
class CalibrationReport:
    namespace: str
    n_predictions: int
    n_with_outcome: int
    mean_absolute_pct_error: float | None
    directional_accuracy: float | None   # fraction of times sign(predicted move) == sign(actual move)
    mean_confidence_when_correct: float | None
    mean_confidence_when_wrong: float | None


def build_calibration_report(records: list[MemoryRecord]) -> CalibrationReport:
    preds = [r for r in records if r.kind in (MemoryKind.THESIS, MemoryKind.RECOMMENDATION)]
    with_outcome = [r for r in preds if r.outcome is not None]

    if not with_outcome:
        return CalibrationReport(
            namespace=preds[0].namespace if preds else "unknown",
            n_predictions=len(preds), n_with_outcome=0,
            mean_absolute_pct_error=None, directional_accuracy=None,
            mean_confidence_when_correct=None, mean_confidence_when_wrong=None,
        )

    errors = []
    correct_flags = []
    conf_correct: list[float] = []
    conf_wrong: list[float] = []

    for r in with_outcome:
        outcome = r.outcome or {}
        predicted = r.content.get("price_target")
        actual = outcome.get("actual_price")
        entry = outcome.get("price_at_prediction")
        if predicted is None or actual is None or entry is None or entry == 0:
            continue
        pct_error = abs(actual - predicted) / entry
        errors.append(pct_error)

        predicted_direction = predicted - entry
        actual_direction = actual - entry
        is_correct_direction = (predicted_direction >= 0) == (actual_direction >= 0)
        correct_flags.append(is_correct_direction)
        (conf_correct if is_correct_direction else conf_wrong).append(r.confidence)

    return CalibrationReport(
        namespace=with_outcome[0].namespace,
        n_predictions=len(preds),
        n_with_outcome=len(with_outcome),
        mean_absolute_pct_error=sum(errors) / len(errors) if errors else None,
        directional_accuracy=sum(correct_flags) / len(correct_flags) if correct_flags else None,
        mean_confidence_when_correct=sum(conf_correct) / len(conf_correct) if conf_correct else None,
        mean_confidence_when_wrong=sum(conf_wrong) / len(conf_wrong) if conf_wrong else None,
    )
