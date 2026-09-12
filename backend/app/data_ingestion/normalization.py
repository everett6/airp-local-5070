"""
Shared normalization helpers so every connector maps to the same financial-
statement field names (GAAP-aligned) regardless of provider quirks. Centralizing
this is what prevents the "duplicated logic" anti-pattern called out in the
design principles — without it, every agent that reads financials would need
its own provider-specific parsing.
"""
from __future__ import annotations

from typing import Any

CANONICAL_FIELDS = {
    "revenue", "cogs", "operating_income", "net_income", "ebit", "ebitda",
    "operating_cash_flow", "capex", "total_assets", "total_liabilities",
    "total_equity", "current_assets", "current_liabilities", "inventory",
    "total_debt", "net_debt", "shares_outstanding", "avg_shareholders_equity",
    "market_value_equity", "working_capital", "retained_earnings",
}

# Maps common provider field aliases -> canonical names. Extend per-provider.
FIELD_ALIASES: dict[str, dict[str, str]] = {
    "generic_provider_a": {
        "totalRevenue": "revenue",
        "costOfRevenue": "cogs",
        "operatingIncome": "operating_income",
        "netIncome": "net_income",
    },
}


def normalize_financials(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    aliases = FIELD_ALIASES.get(provider, {})
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical_key = aliases.get(key, key)
        if canonical_key in CANONICAL_FIELDS:
            normalized[canonical_key] = float(value) if value is not None else None

    missing = CANONICAL_FIELDS - normalized.keys()
    normalized["_missing_fields"] = sorted(missing)
    normalized["_provider"] = provider
    return normalized
