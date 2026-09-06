"""
Lighter Execution Client.
Wraps lighter-sdk SignerClient for live order placement on Lighter.xyz zkRollup.
Supports both Mainnet (Chain ID 304) and Testnet (Chain ID 300).
"""
import logging
import asyncio
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Optional, Tuple, Dict, Any
import lighter
from app.core.execution import MIN_EXECUTABLE_NOTIONAL_USD, MIN_EXECUTABLE_SIZE_BTC
from app.core.execution.submission import LighterSubmissionReceipt
from app.core.lighter_order_reconciliation import LighterOrderOutcome, LighterOrderReconciler
from app.core.settings_manager import settings_manager

logger = logging.getLogger("lighter_client")

BTC_MARKET_INDEX = 1
BTC_SIZE_DECIMALS = 5   # 10^5 multiplier
BTC_PRICE_DECIMALS = 1  # 10^1 multiplier
MIN_ORDER_NOTIONAL = Decimal(str(MIN_EXECUTABLE_NOTIONAL_USD))
MIN_ORDER_BASE_AMOUNT = int(Decimal(str(MIN_EXECUTABLE_SIZE_BTC)) * (10 ** BTC_SIZE_DECIMALS))


class LighterClient:
    """Handles real order execution on zkLighter."""

    def __init__(self) -> None:
        self._signer: Optional[lighter.SignerClient] = None
        self._last_signer_key: Optional[Tuple[str, int, int, str]] = None
        self._lock = asyncio.Lock()

    def _get_base_url_and_chain_id(self) -> Tuple[str, int]:
        if settings_manager.network == "testnet":
            return "https://testnet.zklighter.elliot.ai", 300
        return "https://mainnet.zklighter.elliot.ai", 304

    def _get_signer(self) -> Optional[lighter.SignerClient]:
        """Lazily initializes or updates the SignerClient if credentials change."""
        url, chain_id = self._get_base_url_and_chain_id()
        account_idx = settings_manager.account_index
        key_idx = settings_manager.api_key_index
        priv_key = settings_manager.api_private_key

        if account_idx <= 0 or not priv_key or len(priv_key) < 10:
            return None

        current_key = (url, account_idx, key_idx, priv_key)
        if self._signer is not None and self._last_signer_key == current_key:
            return self._signer

        try:
            self._signer = lighter.SignerClient(
                url=url,
                account_index=account_idx,
                api_private_keys={key_idx: priv_key},
                chain_id=chain_id,
            )
            self._last_signer_key = current_key
            logger.info("Initialized Lighter SignerClient for Account #%s (Key Index %s, Chain %s)", account_idx, key_idx, chain_id)
            return self._signer
        except Exception as e:
            logger.error("Failed to initialize Lighter SignerClient: %s", e)
            return None

    @staticmethod
    def _validate_order_values(size_btc: float, limit_price: float) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Convert order values without rounding a requested quantity above its risk cap."""
        try:
            size = Decimal(str(size_btc))
            price = Decimal(str(limit_price))
        except (InvalidOperation, TypeError, ValueError):
            return None, None, "Order size and limit price must be valid numbers."
        if not size.is_finite() or not price.is_finite() or size <= 0 or price <= 0:
            return None, None, "Order size and limit price must be positive."

        base_amount = int((size * (10 ** BTC_SIZE_DECIMALS)).to_integral_value(rounding=ROUND_DOWN))
        scaled_price = price * (10 ** BTC_PRICE_DECIMALS)
        if scaled_price != scaled_price.to_integral_value():
            return None, None, "Limit price does not match Lighter's 0.1 USD price increment."
        if base_amount < MIN_ORDER_BASE_AMOUNT:
            return None, None, "Order size is below Lighter's 0.00010 BTC minimum."

        executable_size = Decimal(base_amount) / (10 ** BTC_SIZE_DECIMALS)
        if executable_size * price <= MIN_ORDER_NOTIONAL:
            return None, None, "Order notional must be strictly greater than 10.00 USDC."
        return base_amount, int(scaled_price), None

    async def open_snipe_order(
        self,
        *,
        side: str,
        size_btc: float,
        limit_price: float,
        trade_id: int,
        client_order_index: Optional[int] = None,
    ) -> LighterSubmissionReceipt:
        """
        Executes a live market IOC snipe order bounded by the displayed-price limit.
        A valid Lighter ``RespSendTx`` code and transaction hash are required
        before reconciliation can begin.
        """
        async with self._lock:
            # Re-check after waiting for the submission lock.  This prevents an
            # entry queued behind another order from crossing the pause boundary.
            if not settings_manager.trading_enabled:
                return LighterSubmissionReceipt.failure("Global trading activity is paused; live entry was not submitted.")
            signer = self._get_signer()
            if not signer:
                return LighterSubmissionReceipt.failure("SignerClient not initialized: check account index and API key.")

            is_ask = (side.upper() == "SHORT")
            base_amount, price, validation_error = self._validate_order_values(size_btc, limit_price)
            if validation_error:
                return LighterSubmissionReceipt.failure(validation_error)

            try:
                tx, resp, err = await signer.create_order(
                    market_index=BTC_MARKET_INDEX,
                    client_order_index=int(client_order_index if client_order_index is not None else trade_id),
                    base_amount=base_amount,
                    price=price,
                    is_ask=is_ask,
                    order_type=signer.ORDER_TYPE_MARKET,
                    time_in_force=signer.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                    reduce_only=False,
                    order_expiry=signer.DEFAULT_IOC_EXPIRY,
                    api_key_index=settings_manager.api_key_index,
                )
                if err:
                    logger.error("Lighter order submission failed: %s", err)
                    return LighterSubmissionReceipt.failure(err)

                receipt = LighterSubmissionReceipt.from_response(resp)
                if not receipt.success:
                    logger.error("Lighter order acknowledgement rejected: %s", receipt.error)
                    return receipt
                logger.info(
                    "Live Lighter order acknowledged! Trade #%s, client_order_index=%s, side=%s, size=%s BTC, limit=%s, tx=%s, code=%s",
                    trade_id, client_order_index if client_order_index is not None else trade_id,
                    side, size_btc, limit_price, receipt.tx_hash, receipt.response_code,
                )
                return receipt
            except Exception as exc:
                logger.exception("Exception submitting Lighter live order: %s", exc)
                return LighterSubmissionReceipt.failure(exc, uncertain=True)

    async def close_snipe_order(
        self,
        *,
        side: str,
        size_btc: float,
        limit_price: float,
        trade_id: int,
        client_order_index: Optional[int] = None,
    ) -> LighterSubmissionReceipt:
        """
        Executes a live market IOC reduce-only exit bounded by the displayed-price limit.
        A valid Lighter ``RespSendTx`` code and transaction hash are required
        before reconciliation can begin.
        """
        async with self._lock:
            signer = self._get_signer()
            if not signer:
                return LighterSubmissionReceipt.failure("SignerClient not initialized: check account index and API key.")

            # Closing a LONG means selling (is_ask = True); closing a SHORT means buying (is_ask = False)
            is_ask = (side.upper() == "LONG")
            base_amount, price, validation_error = self._validate_order_values(size_btc, limit_price)
            if validation_error:
                return LighterSubmissionReceipt.failure(validation_error)

            try:
                tx, resp, err = await signer.create_order(
                    market_index=BTC_MARKET_INDEX,
                    client_order_index=int(client_order_index if client_order_index is not None else int(trade_id) + 10_000),
                    base_amount=base_amount,
                    price=price,
                    is_ask=is_ask,
                    order_type=signer.ORDER_TYPE_MARKET,
                    time_in_force=signer.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                    reduce_only=True,
                    order_expiry=signer.DEFAULT_IOC_EXPIRY,
                    api_key_index=settings_manager.api_key_index,
                )
                if err:
                    logger.error("Lighter exit order submission failed: %s", err)
                    return LighterSubmissionReceipt.failure(err)

                receipt = LighterSubmissionReceipt.from_response(resp)
                if not receipt.success:
                    logger.error("Lighter exit acknowledgement rejected: %s", receipt.error)
                    return receipt
                logger.info(
                    "Live Lighter exit order acknowledged! Trade #%s, client_order_index=%s, side=%s, tx=%s, code=%s",
                    trade_id, client_order_index if client_order_index is not None else int(trade_id) + 10_000,
                    side, receipt.tx_hash, receipt.response_code,
                )
                return receipt
            except Exception as exc:
                logger.exception("Exception closing Lighter live order: %s", exc)
                return LighterSubmissionReceipt.failure(exc, uncertain=True)

    async def wait_for_order_outcome(
        self,
        *,
        client_order_index: int,
        submitted_at: Optional[float] = None,
        timeout_seconds: float = 2.0,
    ) -> Optional[LighterOrderOutcome]:
        """Await a terminal IOC result from Lighter's authenticated account-order API."""
        signer = self._get_signer()
        if not signer:
            raise RuntimeError("SignerClient not initialized while reconciling a live order.")
        base_url, _ = self._get_base_url_and_chain_id()
        reconciler = LighterOrderReconciler(
            base_url=base_url,
            account_index=settings_manager.account_index,
            signer=signer,
            api_key_index=settings_manager.api_key_index,
        )
        return await reconciler.wait_for_terminal_order(
            client_order_index=client_order_index,
            submitted_at=submitted_at,
            timeout_seconds=timeout_seconds,
        )


# Global Singleton Instance
lighter_client = LighterClient()
