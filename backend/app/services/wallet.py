from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from eth_account import Account

from app.services.wallet_adapters import fetch_hyperliquid, fetch_lighter
from app.services.wallet_models import WalletBalance, WalletValidationError

logger = logging.getLogger(__name__)


class WalletService:
    """Read-only Ethereum wallet state held in process memory.

    The private key is intentionally never persisted or returned by this
    service. It is retained only in memory as preparation for a future,
    separately reviewed signing adapter.
    """

    def __init__(
        self,
        live_trading_enabled: bool = False,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        self.live_trading_enabled = live_trading_enabled
        self._client = client
        self._owns_client = client is None
        self._request_timeout = request_timeout
        self._private_key: str | None = None
        self._address: str | None = None
        self._imported_at: datetime | None = None
        self._refreshed_at: datetime | None = None
        self._balances = self._not_connected_balances()
        self._message = "No wallet imported."
        self._lock = asyncio.Lock()

    async def import_key(self, private_key: str) -> dict[str, Any]:
        normalized = self._normalize_private_key(private_key)
        try:
            account = Account.from_key(normalized)
        except Exception as exc:
            raise WalletValidationError("Invalid Ethereum private key") from exc

        async with self._lock:
            self._private_key = normalized
            self._address = account.address
            self._imported_at = datetime.now(timezone.utc)
            self._refreshed_at = None
            self._balances = self._not_connected_balances("Refreshing…")
            self._message = None
        return self.snapshot()

    async def clear(self) -> dict[str, Any]:
        async with self._lock:
            self._private_key = None
            self._address = None
            self._imported_at = None
            self._refreshed_at = None
            self._balances = self._not_connected_balances()
            self._message = "No wallet imported."
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self._address is not None,
            "address": self._address,
            "imported_at": self._imported_at,
            "refreshed_at": self._refreshed_at,
            "balances": [self._balance_dict(item) for item in self._balances],
            "live_trading_enabled": self.live_trading_enabled,
            "message": self._message,
        }

    async def refresh(self) -> dict[str, Any]:
        async with self._lock:
            if not self._address:
                return self.snapshot()
            address = self._address
            client = self._client_for_request()
            results = await asyncio.gather(
                fetch_hyperliquid(client, address),
                fetch_lighter(client, address),
                return_exceptions=True,
            )
            values = {
                "hyperliquid": self._result_or_error("hyperliquid", results[0]),
                "lighter": self._result_or_error("lighter", results[1]),
                "aevo": WalletBalance(
                    exchange_id="aevo",
                    exchange_name="Aevo",
                    status="not_configured",
                    message="Aevo account balances require Aevo API credentials.",
                ),
            }
            self._balances = [values[key] for key in ("hyperliquid", "aevo", "lighter")]
            self._refreshed_at = datetime.now(timezone.utc)
            self._message = "Read-only balances refreshed."
            return self.snapshot()

    async def close(self) -> None:
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _client_for_request(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._request_timeout)
            self._owns_client = True
        return self._client

    @staticmethod
    def _normalize_private_key(value: str) -> str:
        candidate = value.strip()
        if candidate.startswith("0X"):
            candidate = "0x" + candidate[2:]
        if candidate.startswith("0x"):
            candidate = candidate[2:]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", candidate):
            raise WalletValidationError("Invalid Ethereum private key")
        return f"0x{candidate}"

    @staticmethod
    def _result_or_error(exchange_id: str, result: Any) -> WalletBalance:
        if isinstance(result, WalletBalance):
            return result
        names = {"hyperliquid": "Hyperliquid", "lighter": "Lighter.xyz"}
        logger.warning(
            "wallet balance refresh failed for %s: %s", exchange_id, type(result).__name__
        )
        return WalletBalance(
            exchange_id=exchange_id,
            exchange_name=names[exchange_id],
            status="error",
            message="Balance request failed; try refreshing again.",
        )

    @staticmethod
    def _not_connected_balances(
        message: str = "Import a wallet to fetch balances.",
    ) -> list[WalletBalance]:
        return [
            WalletBalance("hyperliquid", "Hyperliquid", "not_connected", message=message),
            WalletBalance("aevo", "Aevo", "not_connected", message=message),
            WalletBalance("lighter", "Lighter.xyz", "not_connected", message=message),
        ]

    @staticmethod
    def _balance_dict(value: WalletBalance) -> dict[str, Any]:
        return {
            "exchange_id": value.exchange_id,
            "exchange_name": value.exchange_name,
            "status": value.status,
            "total_usd": value.total_usd,
            "available_usd": value.available_usd,
            "assets": [
                {
                    "symbol": asset.symbol,
                    "total": asset.total,
                    "available": asset.available,
                    "locked": asset.locked,
                }
                for asset in (value.assets or [])
            ],
            "message": value.message,
        }
