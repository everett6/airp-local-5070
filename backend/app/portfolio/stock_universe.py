"""Persistent user-selected AI stock-entry restriction; exits and paired ETFs remain possible."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.data_ingestion.tickers import trading_symbol
from app.forward.ledger import write_atomic


def normalize(ticker: str) -> str:
    return trading_symbol(ticker.replace('.', '-').upper())


def activate(queue: dict[str, Any], path: Path) -> None:
    companies = queue['companies']
    if queue.get('profile') != 'tech100' or not companies or len(companies) > 100:
        raise ValueError('Invalid 100-stock queue')
    if any(c.get('sector') != 'Information Technology' for c in companies):
        raise ValueError('Queue contains a company outside Information Technology')
    symbols = sorted({normalize(c['ticker']) for c in companies})
    body = {'version': 1, 'profile': 'tech100', 'symbols': symbols,
            'membership_sha256': queue['source_sha256'], 'scope': 'AI stock entries; paired sector ETFs and exits allowed'}
    body['symbols_sha256'] = hashlib.sha256(json.dumps(symbols).encode()).hexdigest()
    write_atomic(path, json.dumps(body, indent=1) + '\n')


def allowed(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    symbols = data['symbols']
    if data.get('version') != 1 or data.get('profile') != 'tech100' or not isinstance(symbols, list) or not 1 <= len(symbols) <= 100:
        raise ValueError('Invalid AI stock-entry policy')
    if any(not isinstance(s, str) or not s or normalize(s) != s for s in symbols):
        raise ValueError('Invalid policy symbols')
    if hashlib.sha256(json.dumps(symbols).encode()).hexdigest() != data.get('symbols_sha256'):
        raise ValueError('Stock-entry policy hash mismatch')
    return set(symbols)
