"""Pure parsing of the public Lighter account response used by real-mode metrics."""
from typing import Any, Dict, Mapping, Optional


def empty_lighter_account_balances(*, status: str = "UNREGISTERED") -> Dict[str, Any]:
    """Return display-safe values until a Lighter account snapshot is available."""
    return {
        "lighter_collateral_usd": 0.0,
        "lighter_account_equity_usd": 0.0,
        "lighter_free_margin_usd": 0.0,
        "lighter_margin_used_usd": 0.0,
        "lighter_position_notional_usd": 0.0,
        "lighter_unrealized_pnl_usd": 0.0,
        "lighter_realized_pnl_usd": 0.0,
        "lighter_btc_position_btc": 0.0,
        "lighter_btc_unrealized_pnl_usd": 0.0,
        "lighter_account_index": None,
        "lighter_account_status": status,
        "lighter_account_data_available": False,
        "lighter_account_transaction_time": None,
    }


def parse_lighter_account_response(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize `/api/v1/account` response variants into account-level USD metrics.

    Lighter returns the account in an ``accounts`` list on current mainnet, while
    older responses used ``account``. Lighter defines total account value as
    collateral plus unrealized PnL; ``available_balance`` is the
    exchange-reported free margin.
    """
    account = payload.get("account")
    if not isinstance(account, Mapping):
        accounts = payload.get("accounts", [])
        account = accounts[0] if isinstance(accounts, list) and accounts else None
    if not isinstance(account, Mapping):
        return None

    collateral = _number(account.get("collateral"))
    available = _number(account.get("available_balance"))
    positions = account.get("positions", [])
    positions = positions if isinstance(positions, list) else []
    unrealized = sum(_number(position.get("unrealized_pnl")) for position in positions if isinstance(position, Mapping))
    realized = sum(_number(position.get("realized_pnl")) for position in positions if isinstance(position, Mapping))
    notional = sum(abs(_number(position.get("position_value"))) for position in positions if isinstance(position, Mapping))
    equity = collateral + unrealized
    btc = next(
        (
            position for position in positions
            if isinstance(position, Mapping) and (position.get("market_id") == 1 or position.get("symbol") == "BTC")
        ),
        {},
    )
    index = _integer(account.get("account_index", account.get("index")))
    return {
        "lighter_collateral_usd": collateral,
        "lighter_account_equity_usd": equity,
        "lighter_free_margin_usd": available,
        "lighter_margin_used_usd": max(0.0, collateral - available),
        "lighter_position_notional_usd": notional,
        "lighter_unrealized_pnl_usd": unrealized,
        "lighter_realized_pnl_usd": realized,
        "lighter_btc_position_btc": _signed_position(btc),
        "lighter_btc_unrealized_pnl_usd": _number(btc.get("unrealized_pnl")),
        "lighter_account_index": index,
        "lighter_account_status": "ACTIVE" if _integer(account.get("status")) == 1 else "INACTIVE",
        "lighter_account_data_available": True,
        "lighter_account_transaction_time": account.get("transaction_time"),
    }


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signed_position(position: Mapping[str, Any]) -> float:
    size = _number(position.get("position"))
    sign = _integer(position.get("sign"))
    return -abs(size) if sign == -1 else abs(size)
