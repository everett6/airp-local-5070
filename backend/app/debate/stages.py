from __future__ import annotations

from enum import Enum


class DebateStage(str, Enum):
    PLANNING = "planning"
    INDEPENDENT_ANALYSIS = "independent_analysis"
    BULL_THESIS = "bull_thesis"
    BEAR_THESIS = "bear_thesis"
    CROSS_EXAMINATION = "cross_examination"
    MACRO_CHALLENGE = "macro_challenge"
    RISK_CHALLENGE = "risk_challenge"
    PORTFOLIO_REVIEW = "portfolio_review"
    CONSENSUS = "consensus"
    CONFIDENCE_ESTIMATION = "confidence_estimation"
    DONE = "done"


STAGE_ORDER: list[DebateStage] = [
    DebateStage.PLANNING,
    DebateStage.INDEPENDENT_ANALYSIS,
    DebateStage.BULL_THESIS,
    DebateStage.BEAR_THESIS,
    DebateStage.CROSS_EXAMINATION,
    DebateStage.MACRO_CHALLENGE,
    DebateStage.RISK_CHALLENGE,
    DebateStage.PORTFOLIO_REVIEW,
    DebateStage.CONSENSUS,
    DebateStage.CONFIDENCE_ESTIMATION,
    DebateStage.DONE,
]


def next_stage(current: DebateStage) -> DebateStage:
    idx = STAGE_ORDER.index(current)
    if idx == len(STAGE_ORDER) - 1:
        return current
    return STAGE_ORDER[idx + 1]


def compute_agreement(bull_price_target: float, bear_price_target: float, current_price: float) -> float:
    """A simple, transparent agreement metric: how much of the bull/bear price-
    target spread is 'gap' relative to the current price. 1.0 == theses imply
    the same target (rare/suspicious — flagged elsewhere as a lack of genuine
    adversarial testing); 0.0 == spread as wide as the price itself."""
    if current_price <= 0:
        return 0.0
    spread = abs(bull_price_target - bear_price_target)
    return max(0.0, 1.0 - spread / current_price)
