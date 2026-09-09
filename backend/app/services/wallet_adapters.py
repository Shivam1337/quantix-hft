from __future__ import annotations

from typing import Any

import httpx

from app.services.wallet_models import WalletAsset, WalletBalance

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
LIGHTER_API_URL = "https://mainnet.zklighter.elliot.ai/api/v1"


async def fetch_hyperliquid(client: httpx.AsyncClient, address: str) -> WalletBalance:
    perp = await _post_json(
        client,
        HYPERLIQUID_INFO_URL,
        {"type": "clearinghouseState", "user": address},
    )
    summary = perp.get("marginSummary") or perp.get("crossMarginSummary") or {}
    total = _number(summary.get("accountValue"))
    available = _number(perp.get("withdrawable"))
    if total is None:
        raise ValueError("Hyperliquid account value was missing")

    assets: list[WalletAsset] = []
    spot_message = None
    try:
        spot = await _post_json(
            client,
            HYPERLIQUID_INFO_URL,
            {"type": "spotClearinghouseState", "user": address},
        )
        for row in spot.get("balances", []):
            if not isinstance(row, dict):
                continue
            amount = _number(row.get("total"))
            if amount is not None and amount != 0:
                held = _number(row.get("hold"))
                assets.append(
                    WalletAsset(
                        symbol=str(row.get("coin") or "asset"),
                        total=amount,
                        available=(amount - held) if held is not None else None,
                        locked=held,
                    )
                )
    except Exception:
        spot_message = "Perpetual value available; spot assets could not be loaded."

    return WalletBalance(
        exchange_id="hyperliquid",
        exchange_name="Hyperliquid",
        status="connected",
        total_usd=total,
        available_usd=available,
        assets=assets,
        message=spot_message,
    )


async def fetch_lighter(client: httpx.AsyncClient, address: str) -> WalletBalance:
    payload = await _get_json(
        client,
        f"{LIGHTER_API_URL}/account",
        params={"by": "l1_address", "value": address},
    )
    accounts = payload.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        return WalletBalance(
            exchange_id="lighter",
            exchange_name="Lighter.xyz",
            status="no_account",
            message="No Lighter account was found for this address.",
        )
    account = next(
        (row for row in accounts if isinstance(row, dict) and str(row.get("account_type")) == "0"),
        next((row for row in accounts if isinstance(row, dict)), None),
    )
    if account is None:
        raise ValueError("Lighter account response was invalid")

    collateral = _number(account.get("collateral"))
    if collateral is None:
        raise ValueError("Lighter collateral was missing")
    available = _number(account.get("available_balance"))
    return WalletBalance(
        exchange_id="lighter",
        exchange_name="Lighter.xyz",
        status="connected",
        total_usd=collateral,
        available_usd=available if available is not None else collateral,
        assets=_lighter_assets(account.get("assets")),
    )


async def _post_json(
    client: httpx.AsyncClient, url: str, payload: dict[str, str]
) -> dict[str, Any]:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("exchange response was not an object")
    return value


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, str]
) -> dict[str, Any]:
    response = await client.get(url, params=params)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("exchange response was not an object")
    return value


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lighter_assets(rows: Any) -> list[WalletAsset]:
    if not isinstance(rows, list):
        return []
    assets: list[WalletAsset] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        total = _number(row.get("balance"))
        if total is None or total == 0:
            continue
        locked = _number(row.get("locked_balance"))
        assets.append(
            WalletAsset(
                symbol=str(row.get("symbol") or "asset"),
                total=total,
                available=(total - locked) if locked is not None else None,
                locked=locked,
            )
        )
    return assets
