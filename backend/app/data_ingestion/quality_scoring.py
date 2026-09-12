"""
Composable data quality scoring. `base_connector.py` calls
`score_from_issues` for the generic case; ingestion pipelines that also know
about freshness/completeness (e.g. financial statements with missing fields)
should call `composite_quality_score` for a fuller picture, which then flows
into `EvidenceRef.data_quality_score` and ultimately the confidence engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class QualityFactors:
    completeness: float       # fraction of expected fields present, 0..1
    freshness: float          # 1.0 = just fetched, decays with staleness
    validation_penalty: float  # 0..1, 0 = no issues
    provider_reliability: float  # static per-provider prior, 0..1


def freshness_score(fetched_at: datetime, max_age_seconds: int) -> float:
    age = (datetime.now(UTC) - fetched_at).total_seconds()
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max_age_seconds)


def composite_quality_score(factors: QualityFactors) -> float:
    weights = {"completeness": 0.35, "freshness": 0.25, "validation": 0.25, "provider": 0.15}
    score = (
        weights["completeness"] * factors.completeness
        + weights["freshness"] * factors.freshness
        + weights["validation"] * (1.0 - factors.validation_penalty)
        + weights["provider"] * factors.provider_reliability
    )
    return round(max(0.0, min(1.0, score)), 4)
