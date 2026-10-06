"""Read-only, allowlisted paper account details for the desktop account tab."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.portfolio.broker import PAPER, Alpaca, BrokerError

FIELDS = (
    "status", "currency", "created_at", "equity", "last_equity", "portfolio_value", "cash",
    "buying_power", "regt_buying_power", "daytrading_buying_power", "non_marginable_buying_power",
    "initial_margin", "maintenance_margin", "last_maintenance_margin", "multiplier",
    "long_market_value", "short_market_value", "accrued_fees", "pending_transfer_in",
    "pending_transfer_out", "pattern_day_trader", "daytrade_count", "shorting_enabled",
    "trading_blocked", "transfers_blocked", "account_blocked", "trade_suspended_by_user",
    "options_approved_level", "options_trading_level", "options_buying_power",
)


def snapshot(client: Alpaca, full: bool = False) -> dict[str, Any]:
    account = client._get(f"{PAPER}/account")
    result: dict[str, Any] = {"environment": "paper", "at": datetime.now(UTC).isoformat(),
            "account": {k: account[k] for k in FIELDS if k in account},
            "account_number": "••••" + str(account["account_number"])[-4:] if account.get("account_number") else None}
    if full:
        endpoints: dict[str, tuple[str, dict[str, Any]]] = {'positions': (f'{PAPER}/positions', {}),
                     'orders': (f'{PAPER}/orders', {'status': 'all', 'limit': 100, 'direction': 'desc'}),
                     'history': (f'{PAPER}/account/portfolio/history', {'period': '1M', 'timeframe': '1D'})}
        fields = {'positions': ('symbol', 'qty', 'side', 'market_value', 'current_price', 'avg_entry_price', 'unrealized_pl', 'unrealized_plpc'),
                  'orders': ('symbol', 'side', 'qty', 'filled_qty', 'filled_avg_price', 'status', 'type', 'time_in_force', 'submitted_at', 'filled_at', 'client_order_id'),
                  'history': ('timestamp', 'equity', 'profit_loss', 'profit_loss_pct', 'base_value', 'timeframe')}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(client._get, url, **params) for name, (url, params) in endpoints.items()}
            for name, future in futures.items():
                try:
                    data = future.result()
                    if name == 'history':
                        if not isinstance(data, dict):
                            raise ValueError('Invalid history')
                        result[name] = {k: data[k] for k in fields[name] if k in data}
                    else:
                        if not isinstance(data, list):
                            raise ValueError('Invalid collection')
                        result[name] = [{k: row[k] for k in fields[name] if k in row} for row in data[:500]]
                except (BrokerError, httpx.HTTPError, ValueError, OSError, TypeError):
                    result[name + '_error'] = 'Could not read ' + name + '; other account sections may be available.'
    return result


def main() -> None:
    client = None
    try:
        if "--auto" in sys.argv:  # the autopilot's separate paper account (read-only)
            from app.data_ingestion.bars import key
            k, s = key("AIRP_AUTO_ALPACA_KEY_ID"), key("AIRP_AUTO_ALPACA_SECRET_KEY")
            client = Alpaca(k, s, PAPER, risk_policy=None) if k and s else None
        else:
            client = Alpaca.from_env()
        result = snapshot(client, full=True) if client else {"error": "This Alpaca paper account is not configured."}
    except (BrokerError, httpx.HTTPError, ValueError, OSError):
        result = {"error": "Could not read the Alpaca paper account. Check the connection and account configuration."}
    finally:
        if client is not None:
            client.c.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
