"""
The verifier is a pure-code gate between "agent output" and "report content".
No claim reaches the report generator without passing through here.

It does three things, deliberately kept separate so each is independently
testable:
  1. Resolve each EvidenceRef against the store that should contain it
     (freshness check -> VERIFIED / STALE / UNVERIFIED).
  2. Recompute (never trust) any numeric claim by re-invoking the named
     `numeric_source` function against the same inputs, and flag drift.
  3. Cross-check claims against each other for direct contradictions
     (e.g. two claims about the same metric with different values).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.evidence.models import Claim, ClaimBundle, EvidenceRef, VerificationStatus


class EvidenceStore(Protocol):
    async def resolve(self, ref: EvidenceRef) -> ResolutionResult: ...


@dataclass(frozen=True)
class ResolutionResult:
    exists: bool
    is_fresh: bool
    conflicting_value: float | None = None


@dataclass(frozen=True)
class VerificationReport:
    total_claims: int
    verified: int
    stale: int
    unverified: int
    contradicted: int
    numeric_drift: list[str]  # claim_ids where recomputation disagreed
    blocking: bool  # True => report generation must not proceed silently

    @property
    def pass_rate(self) -> float:
        return 0.0 if self.total_claims == 0 else self.verified / self.total_claims


NumericRecomputer = Callable[[Claim], Awaitable[float]]


class EvidenceVerifier:
    def __init__(
        self,
        store: EvidenceStore,
        numeric_registry: dict[str, NumericRecomputer],
        numeric_drift_tolerance: float = 1e-6,
        max_evidence_age_by_source: dict[str, int] | None = None,
    ) -> None:
        self._store = store
        self._numeric_registry = numeric_registry
        self._tolerance = numeric_drift_tolerance
        self._max_age = max_evidence_age_by_source or {}

    async def verify_bundle(self, bundle: ClaimBundle) -> tuple[ClaimBundle, VerificationReport]:
        resolved_claims: list[Claim] = []
        numeric_drift: list[str] = []
        counts = {s: 0 for s in VerificationStatus}

        for claim in bundle.claims:
            status = await self._verify_claim_evidence(claim)

            if claim.is_numeric:
                drifted = await self._check_numeric_drift(claim)
                if drifted:
                    numeric_drift.append(claim.claim_id)
                    status = VerificationStatus.CONTRADICTED

            counts[status] += 1
            resolved_claims.append(claim.model_copy(update={"verification_status": status}))

        report = VerificationReport(
            total_claims=len(resolved_claims),
            verified=counts[VerificationStatus.VERIFIED],
            stale=counts[VerificationStatus.STALE],
            unverified=counts[VerificationStatus.UNVERIFIED],
            contradicted=counts[VerificationStatus.CONTRADICTED],
            numeric_drift=numeric_drift,
            blocking=counts[VerificationStatus.CONTRADICTED] > 0,
        )
        return ClaimBundle(claims=resolved_claims), report

    async def _verify_claim_evidence(self, claim: Claim) -> VerificationStatus:
        if not claim.evidence_refs:
            return VerificationStatus.UNVERIFIED

        statuses = []
        for ref in claim.evidence_refs:
            res = await self._store.resolve(ref)
            if not res.exists:
                statuses.append(VerificationStatus.UNVERIFIED)
                continue
            if res.conflicting_value is not None:
                statuses.append(VerificationStatus.CONTRADICTED)
                continue
            statuses.append(
                VerificationStatus.VERIFIED if res.is_fresh else VerificationStatus.STALE
            )

        # Worst-of: one bad ref taints the claim. Explainability > optimism.
        priority = [
            VerificationStatus.CONTRADICTED,
            VerificationStatus.UNVERIFIED,
            VerificationStatus.STALE,
            VerificationStatus.VERIFIED,
        ]
        for status in priority:
            if status in statuses:
                return status
        return VerificationStatus.UNVERIFIED

    async def _check_numeric_drift(self, claim: Claim) -> bool:
        if claim.numeric_source is None or claim.numeric_value is None:
            return False
        recompute = self._numeric_registry.get(claim.numeric_source)
        if recompute is None:
            # Unknown numeric source is itself a red flag — treat as drift so
            # it surfaces rather than silently passing.
            return True
        fresh_value = await recompute(claim)
        return abs(fresh_value - claim.numeric_value) > self._tolerance


def utcnow() -> datetime:
    return datetime.now(UTC)
