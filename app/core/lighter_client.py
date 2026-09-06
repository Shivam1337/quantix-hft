"""
Lighter Execution Client.
Wraps lighter-sdk SignerClient for live order placement on Lighter.xyz zkRollup.
Supports both Mainnet (Chain ID 304) and Testnet (Chain ID 300).
"""
import logging
import asyncio
from typing import Optional, Tuple, Dict, Any
import lighter
from app.core.settings_manager import settings_manager

logger = logging.getLogger("lighter_client")

BTC_MARKET_INDEX = 1
BTC_SIZE_DECIMALS = 5   # 10^5 multiplier
BTC_PRICE_DECIMALS = 1  # 10^1 multiplier


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

    async def open_snipe_order(
        self,
        *,
        side: str,
        size_btc: float,
        slippage_limit_px: float,
        trade_id: int,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Executes a live market IOC snipe order on Lighter.xyz.
        Returns: (success: bool, tx_hash: Optional[str], error_message: Optional[str])
        """
        async with self._lock:
            signer = self._get_signer()
            if not signer:
                return False, None, "SignerClient not initialized: check account index and API key."

            is_ask = (side.upper() == "SHORT")
            base_amount = int(round(size_btc * (10 ** BTC_SIZE_DECIMALS)))
            price = int(round(slippage_limit_px * (10 ** BTC_PRICE_DECIMALS)))

            try:
                tx, resp, err = await signer.create_order(
                    market_index=BTC_MARKET_INDEX,
                    client_order_index=int(trade_id),
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
                    return False, None, str(err)

                tx_hash = resp.tx_hash if hasattr(resp, "tx_hash") else str(resp)
                logger.info("Live Lighter order submitted! Trade #%s, side=%s, size=%s BTC, tx=%s", trade_id, side, size_btc, tx_hash)
                return True, tx_hash, None
            except Exception as exc:
                logger.exception("Exception submitting Lighter live order: %s", exc)
                return False, None, str(exc)

    async def close_snipe_order(
        self,
        *,
        side: str,
        size_btc: float,
        slippage_limit_px: float,
        trade_id: int,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Executes a live market IOC reduce-only exit order on Lighter.xyz.
        Returns: (success: bool, tx_hash: Optional[str], error_message: Optional[str])
        """
        async with self._lock:
            signer = self._get_signer()
            if not signer:
                return False, None, "SignerClient not initialized: check account index and API key."

            # Closing a LONG means selling (is_ask = True); closing a SHORT means buying (is_ask = False)
            is_ask = (side.upper() == "LONG")
            base_amount = int(round(size_btc * (10 ** BTC_SIZE_DECIMALS)))
            price = int(round(slippage_limit_px * (10 ** BTC_PRICE_DECIMALS)))

            try:
                tx, resp, err = await signer.create_order(
                    market_index=BTC_MARKET_INDEX,
                    client_order_index=int(trade_id) + 10_000,
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
                    return False, None, str(err)

                tx_hash = resp.tx_hash if hasattr(resp, "tx_hash") else str(resp)
                logger.info("Live Lighter position closed! Trade #%s, side=%s, tx=%s", trade_id, side, tx_hash)
                return True, tx_hash, None
            except Exception as exc:
                logger.exception("Exception closing Lighter live order: %s", exc)
                return False, None, str(exc)


# Global Singleton Instance
lighter_client = LighterClient()
