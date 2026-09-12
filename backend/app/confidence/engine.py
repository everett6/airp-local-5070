"""
Confidence scoring is a *weighted composite of independently computed
sub-scores*, never a single LLM-asserted "I am 85% confident". Each sub-score
has a documented, reproducible formula so the final number can be decomposed
and challenged in the report itself (see reports/generator.py -> Confidence
Assessment section).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.evidence.verifier import VerificationReport


@dataclass(frozen=True)
class ConfidenceInputs:
    verification_report: VerificationReport
    data_quality_scores: list[float]         # from each ingestion source used, 0..1
    bull_bear_agreement: float                # 0..1, see debate/stages.compute_agreement
    num_unresolved_challenges: int
    reasoning_depth_score: float              # 0..1, from debate transcript structure
    market_regime_uncertainty: float          # 0..1, higher = more macro/vol uncertainty


@dataclass(frozen=True)
class ConfidenceBreakdown:
    evidence_quality: float
    data_quality: float
    reasoning_quality: float
    consensus_strength: float
    market_uncertainty_penalty: float
    overall_confidence: float
    weights_used: dict[str, float]
    caveats: list[str]


DEFAULT_WEIGHTS = {
    "evidence_quality": 0.30,
    "data_quality": 0.20,
    "reasoning_quality": 0.20,
    "consensus_strength": 0.20,
    "market_uncertainty_penalty": 0.10,
}


def compute_confidence(
    inputs: ConfidenceInputs, weights: dict[str, float] | None = None
) -> ConfidenceBreakdown:
    w = weights or DEFAULT_WEIGHTS
    caveats: list[str] = []

    evidence_quality = inputs.verification_report.pass_rate
    if inputs.verification_report.unverified > 0:
        caveats.append(
            f"{inputs.verification_report.unverified} claim(s) could not be verified "
            "against a source and are marked UNVERIFIED in the report."
        )
    if inputs.verification_report.contradicted > 0:
        caveats.append(
            f"{inputs.verification_report.contradicted} claim(s) were CONTRADICTED by "
            "recomputation or a conflicting source — treat the affected section with caution."
        )

    data_quality = (
        sum(inputs.data_quality_scores) / len(inputs.data_quality_scores)
        if inputs.data_quality_scores else 0.0
    )
    if data_quality < 0.7:
        caveats.append("Average upstream data quality score is below 0.70.")

    consensus_strength = max(
        0.0, inputs.bull_bear_agreement - 0.05 * inputs.num_unresolved_challenges
    )
    if inputs.num_unresolved_challenges > 0:
        caveats.append(
            f"{inputs.num_unresolved_challenges} cross-examination challenge(s) were "
            "not resolved to consensus during the debate stage."
        )

    reasoning_quality = inputs.reasoning_depth_score
    uncertainty_penalty = 1.0 - inputs.market_regime_uncertainty
    if inputs.market_regime_uncertainty > 0.6:
        caveats.append("Elevated macro/volatility regime uncertainty at time of analysis.")

    overall = (
        w["evidence_quality"] * evidence_quality
        + w["data_quality"] * data_quality
        + w["reasoning_quality"] * reasoning_quality
        + w["consensus_strength"] * consensus_strength
        + w["market_uncertainty_penalty"] * uncertainty_penalty
    )

    return ConfidenceBreakdown(
        evidence_quality=evidence_quality,
        data_quality=data_quality,
        reasoning_quality=reasoning_quality,
        consensus_strength=consensus_strength,
        market_uncertainty_penalty=uncertainty_penalty,
        overall_confidence=round(overall, 4),
        weights_used=dict(w),
        caveats=caveats,
    )
