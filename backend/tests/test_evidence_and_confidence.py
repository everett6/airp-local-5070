from datetime import UTC, datetime

import pytest

from app.confidence.engine import ConfidenceInputs, compute_confidence
from app.evidence.models import Claim, ClaimBundle, EvidenceRef, SourceType
from app.evidence.verifier import EvidenceVerifier, ResolutionResult


class FakeStore:
    def __init__(self, exists=True, is_fresh=True, conflicting_value=None):
        self.exists = exists
        self.is_fresh = is_fresh
        self.conflicting_value = conflicting_value

    async def resolve(self, ref):
        return ResolutionResult(exists=self.exists, is_fresh=self.is_fresh,
                                 conflicting_value=self.conflicting_value)


def make_claim(numeric=False, with_evidence=True, value=1.0, numeric_source="mod.fn"):
    refs = [EvidenceRef(
        evidence_id="e1", source_type=SourceType.FINANCIAL_STATEMENT, source_id="s1",
        retrieved_at=datetime.now(UTC),
    )] if with_evidence else []
    return Claim(
        claim_id="c1", text="test claim", made_by="fundamental_analyst",
        evidence_refs=refs, is_numeric=numeric, numeric_value=value if numeric else None,
        numeric_source=numeric_source if numeric else None,
    )


def test_numeric_claim_requires_source():
    with pytest.raises(ValueError):
        Claim(claim_id="c2", text="bad", made_by="x", is_numeric=True, numeric_value=5.0)


@pytest.mark.asyncio
async def test_verifier_marks_verified_when_resolvable_and_fresh():
    verifier = EvidenceVerifier(store=FakeStore(exists=True, is_fresh=True), numeric_registry={})
    bundle = ClaimBundle(claims=[make_claim(with_evidence=True)])
    _resolved, report = await verifier.verify_bundle(bundle)
    assert report.verified == 1
    assert report.unverified == 0


@pytest.mark.asyncio
async def test_verifier_marks_unverified_when_no_evidence():
    verifier = EvidenceVerifier(store=FakeStore(), numeric_registry={})
    bundle = ClaimBundle(claims=[make_claim(with_evidence=False)])
    _resolved, report = await verifier.verify_bundle(bundle)
    assert report.unverified == 1
    assert report.blocking is False


@pytest.mark.asyncio
async def test_verifier_flags_numeric_drift():
    async def recompute(claim):
        return 999.0  # deliberately different from claim's numeric_value

    verifier = EvidenceVerifier(
        store=FakeStore(exists=True, is_fresh=True),
        numeric_registry={"mod.fn": recompute},
    )
    bundle = ClaimBundle(claims=[make_claim(numeric=True, value=1.0)])
    _resolved, report = await verifier.verify_bundle(bundle)
    assert report.contradicted == 1
    assert report.blocking is True
    assert "c1" in report.numeric_drift


def test_confidence_engine_penalizes_unverified_and_uncertainty():
    from app.evidence.verifier import VerificationReport

    good_report = VerificationReport(total_claims=10, verified=10, stale=0, unverified=0,
                                      contradicted=0, numeric_drift=[], blocking=False)
    bad_report = VerificationReport(total_claims=10, verified=4, stale=1, unverified=5,
                                     contradicted=0, numeric_drift=[], blocking=False)

    good = compute_confidence(ConfidenceInputs(
        verification_report=good_report, data_quality_scores=[0.95, 0.9],
        bull_bear_agreement=0.8, num_unresolved_challenges=0,
        reasoning_depth_score=0.9, market_regime_uncertainty=0.1,
    ))
    bad = compute_confidence(ConfidenceInputs(
        verification_report=bad_report, data_quality_scores=[0.5, 0.4],
        bull_bear_agreement=0.3, num_unresolved_challenges=3,
        reasoning_depth_score=0.4, market_regime_uncertainty=0.8,
    ))

    assert good.overall_confidence > bad.overall_confidence
    assert len(bad.caveats) > len(good.caveats)
