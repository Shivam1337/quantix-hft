from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import SystemSetting


class SettingsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], defaults: Settings):
        self.session_factory = session_factory
        self.defaults = defaults

    async def get(self) -> SystemSetting:
        async with self.session_factory() as session:
            setting = await session.get(SystemSetting, 1)
            if setting is None:
                setting = SystemSetting(
                    id=1,
                    min_apr=self.defaults.default_min_apr,
                    min_open_interest=self.defaults.default_min_open_interest,
                    basis_threshold_bps=self.defaults.basis_threshold_bps,
                    auto_unwind=self.defaults.auto_unwind,
                    entry_min_history_snapshots=self.defaults.entry_min_history_snapshots,
                    entry_min_spread_stability_pct=self.defaults.entry_min_spread_stability_pct,
                    alert_webhook_url=self.defaults.alert_webhook_url,
                )
                session.add(setting)
                await session.commit()
            return setting

    async def update(self, values: dict) -> SystemSetting:
        async with self.session_factory() as session:
            setting = await session.get(SystemSetting, 1)
            if setting is None:
                setting = SystemSetting(id=1)
                session.add(setting)
            for key, value in values.items():
                if value is not None or key == "alert_webhook_url":
                    setattr(setting, key, value)
            await session.commit()
            return setting
