"""Authenticated, asynchronous reconciliation of terminal Lighter IOC orders."""
import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

import aiohttp


TERMINAL_STATUSES = frozenset({
    "filled",
    "canceled",
    "canceled-post-only",
    "canceled-reduce-only",
    "canceled-position-not-allowed",
    "canceled-margin-not-allowed",
    "canceled-too-much-slippage",
    "canceled-not-enough-liquidity",
    "canceled-self-trade",
    "canceled-expired",
    "canceled-oco",
    "canceled-child",
    "canceled-liquidation",
    "canceled-invalid-balance",
})


@dataclass(frozen=True)
class LighterOrderOutcome:
    """Terminal IOC result in human BTC/USDC units returned by Lighter's API."""

    client_order_index: int
    status: str
    filled_size_btc: float
    filled_quote_usd: float
    average_fill_price: Optional[float]
    exchange_timestamp: Optional[float]

    @property
    def has_fill(self) -> bool:
        return self.filled_size_btc > 0.0


def is_terminal_status(status: Any) -> bool:
    return str(status or "").strip().lower() in TERMINAL_STATUSES


def _as_decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() and parsed >= 0 else Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _epoch_seconds(value: Any) -> Optional[float]:
    parsed = _as_decimal(value)
    if parsed <= 0:
        return None
    if parsed >= Decimal("100000000000000"):
        parsed /= Decimal("1000000")
    elif parsed >= Decimal("100000000000"):
        parsed /= Decimal("1000")
    return float(parsed)


def order_outcome_from_api(order: Mapping[str, Any]) -> Optional[LighterOrderOutcome]:
    """Parse one Lighter order object, retaining only terminal outcomes."""
    status = str(order.get("status", "")).strip().lower()
    if not is_terminal_status(status):
        return None
    try:
        client_order_index = int(order["client_order_index"])
    except (KeyError, TypeError, ValueError):
        return None

    filled_size = _as_decimal(order.get("filled_base_amount", "0"))
    filled_quote = _as_decimal(order.get("filled_quote_amount", "0"))
    average_price = None
    if filled_size > 0:
        average_price = float(filled_quote / filled_size) if filled_quote > 0 else float(_as_decimal(order.get("price", "0")))
        if average_price <= 0:
            average_price = None
    exchange_timestamp = next(
        (
            normalized
            for raw in (
                order.get("transaction_time"),
                order.get("updated_at"),
                order.get("timestamp"),
                order.get("created_at"),
            )
            if (normalized := _epoch_seconds(raw)) is not None
        ),
        None,
    )
    return LighterOrderOutcome(
        client_order_index=client_order_index,
        status=status,
        filled_size_btc=float(filled_size),
        filled_quote_usd=float(filled_quote),
        average_fill_price=average_price,
        exchange_timestamp=exchange_timestamp,
    )


async def wait_for_terminal_order(
    fetch_orders: Callable[[], Awaitable[Iterable[Mapping[str, Any]]]],
    *,
    client_order_index: int,
    timeout_seconds: float,
    not_before_epoch: Optional[float] = None,
    poll_interval_seconds: float = 0.10,
) -> Optional[LighterOrderOutcome]:
    """Poll an authenticated account snapshot until this IOC reaches a terminal state."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        for order in await fetch_orders():
            outcome = order_outcome_from_api(order)
            is_current_submission = (
                not_before_epoch is None
                or outcome is None
                or outcome.exchange_timestamp is None
                or outcome.exchange_timestamp >= not_before_epoch - 15.0
            )
            if (
                outcome
                and outcome.client_order_index == int(client_order_index)
                and is_current_submission
            ):
                return outcome
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


class LighterOrderReconciler:
    """Uses Lighter's signed account-order endpoints without blocking market callbacks."""

    def __init__(self, *, base_url: str, account_index: int, signer: Any, api_key_index: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_index = int(account_index)
        self.signer = signer
        self.api_key_index = int(api_key_index)

    async def wait_for_terminal_order(
        self,
        *,
        client_order_index: int,
        submitted_at: Optional[float] = None,
        timeout_seconds: float = 2.0,
    ) -> Optional[LighterOrderOutcome]:
        authorization, error = self.signer.create_auth_token_with_expiry(
            api_key_index=self.api_key_index,
        )
        if error:
            raise RuntimeError(f"Could not create Lighter order-query authorization: {error}")
        timeout = aiohttp.ClientTimeout(total=2.0)
        headers = {"authorization": authorization, "accept": "application/json"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async def fetch_orders() -> Iterable[Mapping[str, Any]]:
                inactive = await self._get_orders(session, "accountInactiveOrders", limit=100)
                match = [
                    order for order in inactive
                    if str(order.get("client_order_index")) == str(client_order_index)
                ]
                has_current_match = any(
                    (outcome := order_outcome_from_api(order))
                    and (
                        submitted_at is None
                        or outcome.exchange_timestamp is None
                        or outcome.exchange_timestamp >= submitted_at - 15.0
                    )
                    for order in match
                )
                if has_current_match:
                    return match
                active = await self._get_orders(session, "accountActiveOrders")
                return [*match, *active]

            return await wait_for_terminal_order(
                fetch_orders,
                client_order_index=client_order_index,
                timeout_seconds=timeout_seconds,
                not_before_epoch=submitted_at,
            )

    async def _get_orders(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        *,
        limit: Optional[int] = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, Any] = {
            "account_index": self.account_index,
            "market_id": 1,
        }
        if limit is not None:
            params["limit"] = limit
        async with session.get(f"{self.base_url}/api/v1/{endpoint}", params=params) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        orders = payload.get("orders", []) if isinstance(payload, dict) else []
        return [order for order in orders if isinstance(order, Mapping)]
