import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AlertLog

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], webhook_url: str | None = None
    ):
        self.session_factory = session_factory
        self.webhook_url = webhook_url

    async def send(self, event: str, message: str, position_id: str | None = None) -> None:
        async with self.session_factory() as session:
            session.add(
                AlertLog(position_id=position_id, level="warning", event=event, message=message)
            )
            await session.commit()
        if not self.webhook_url:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    self.webhook_url, json={"event": event, "message": message}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("alert webhook failed: %s", exc)
