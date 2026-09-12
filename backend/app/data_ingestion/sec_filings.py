"""
SEC EDGAR connector for 10-K / 10-Q / 8-K / 13F / Form 4 (insider). Real
implementation hits EDGAR's full-text search + submissions JSON APIs
(https://www.sec.gov/cgi-bin/browse-edgar, data.sec.gov/submissions/) — user
agent string is required by SEC and is pulled from Settings.sec_edgar_user_agent.
This file ships the normalization/validation contract plus a mock adapter so
tests don't hit the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.data_ingestion.base_connector import DataConnector, ValidationIssue


@dataclass(frozen=True)
class Filing:
    accession_number: str
    ticker: str
    cik: str
    filing_type: str  # "10-K" | "10-Q" | "8-K" | "13F" | "4"
    filed_at: str
    period_of_report: str
    url: str
    full_text_excerpt: str = ""


class SECFilingsConnector(DataConnector[list[Filing]]):
    name = "sec_edgar"
    default_ttl_seconds = 3600 * 24 * 7  # filings don't change once filed

    def _source_id(self, **kwargs: Any) -> str:
        return f"filings:{kwargs['ticker']}:{kwargs.get('filing_type', 'all')}"

    def _validate(self, data: list[Filing]) -> list[ValidationIssue]:
        issues = []
        for f in data:
            if not f.accession_number:
                issues.append(ValidationIssue("accession_number", "error", "missing accession number"))
            if f.filing_type not in {"10-K", "10-Q", "8-K", "13F", "4"}:
                issues.append(ValidationIssue("filing_type", "warning", f"unexpected type {f.filing_type}"))
        return issues

    def _normalize(self, raw: list[dict[str, Any]]) -> list[Filing]:
        return [Filing(**r) for r in raw]

    async def _fetch_raw(self, **kwargs: Any) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError("Wire EDGAR submissions API here.")


class MockSECFilingsConnector(SECFilingsConnector):
    name = "mock_sec_edgar"

    async def _fetch_raw(self, **kwargs: Any) -> list[dict[str, Any]]:
        ticker = kwargs["ticker"]
        return [{
            "accession_number": f"0000000000-26-{abs(hash(ticker)) % 999999:06d}",
            "ticker": ticker, "cik": "0000000000", "filing_type": "10-K",
            "filed_at": "2026-02-15", "period_of_report": "2025-12-31",
            "url": f"https://www.sec.gov/mock/{ticker}/10-K",
            "full_text_excerpt": "(mock filing text for local development)",
        }]
