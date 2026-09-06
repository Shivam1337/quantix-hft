"""Low-rate exchange-account refresh independent of market-data callbacks."""
import asyncio
import logging

from app.config import REAL_ACCOUNT_REFRESH_SECONDS
from app.core.settings_manager import settings_manager
from app.core.wallet_manager import wallet_manager


logger = logging.getLogger("real_account_refresh")


async def real_account_refresh_task() -> None:
    """Keep dashboard account equity and free margin fresh while REAL mode is enabled."""
    while True:
        try:
            if settings_manager.is_real_mode:
                await wallet_manager.refresh_balances()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Could not refresh Lighter real-account metrics: %s", exc)
        await asyncio.sleep(REAL_ACCOUNT_REFRESH_SECONDS)
