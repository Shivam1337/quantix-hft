"""Terminal-only coverage for the persisted global trading entry kill switch."""
import asyncio
import copy
import shutil
import tempfile
import time
import unittest

from app.core.lighter_client import LighterClient
from app.core.settings_manager import SettingsManager, settings_manager
from app.core.sniper_engine import SniperEngine


class TradingActivitySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = f"{self.temp_dir}/settings.db"
        self.manager = SettingsManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_global_activity_setting_defaults_enabled_and_persists_pause(self):
        self.assertTrue(self.manager.trading_enabled)

        success, _ = self.manager.set_trading_enabled(False)
        self.assertTrue(success)
        self.assertFalse(self.manager.trading_enabled)

        reloaded = SettingsManager(db_path=self.db_path)
        self.assertFalse(reloaded.trading_enabled)
        self.assertFalse(reloaded.get_summary()["trading_enabled"])


class TradingPauseEngineTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        settings_manager._settings.update({
            "trading_mode": "SIMULATION",
            "trading_enabled": False,
            "min_lag_trigger": 6.0,
            "simulation_starting_balance": 100.0,
            "trade_margin_fraction": 0.50,
            "leverage": 50.0,
        })

    def tearDown(self):
        settings_manager._settings = self.original_settings

    @staticmethod
    def _signal_book(best_bid=99.9, best_ask=100.0):
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": (best_bid + best_ask) / 2,
            "bids": [[str(best_bid), "3.0"]],
            "asks": [[str(best_ask), "3.0"]],
        }

    def test_pause_blocks_new_entries_in_simulation_and_real_modes(self):
        for mode in ("SIMULATION", "REAL"):
            settings_manager._settings["trading_mode"] = mode
            engine = SniperEngine()

            result = engine.process_tick(
                self._signal_book(), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )

            self.assertIsNone(result["active_position"])
            self.assertEqual(0, engine.trade_counter)
            self.assertEqual("PAUSED", result["decision"]["stance"])
            self.assertEqual("GLOBAL_TRADING_PAUSED", result["decision"]["rejection_reason"])
            self.assertFalse(result["trading_enabled"])

    def test_pause_keeps_existing_position_under_exit_management(self):
        engine = SniperEngine()
        engine.active_trade = {
            "id": 1,
            "mode": "SIMULATION",
            "side": "LONG",
            "leader_name": "Binance",
            "entry_leader_px": 110.0,
            "entry_px": 100.0,
            "size": 0.01,
            "target_px": 101.0,
            "stop_loss_px": 80.0,
            "entry_ts": time.time() - 1.0,
            "margin_allocated_usd": 1.0,
            "leverage": 50.0,
            "notional_usd": 1.0,
        }

        result = engine.process_tick(
            self._signal_book(best_bid=101.0, best_ask=101.1),
            "Binance", 110.0, 110.0, 10.0, "HIGH_CONVICTION", "Major venues agree.",
        )

        self.assertIsNone(result["active_position"])
        self.assertEqual(1, len(result["closed_trades"]))


class LiveEntryPauseGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        settings_manager._settings["trading_enabled"] = False

    def tearDown(self):
        settings_manager._settings = self.original_settings

    async def test_live_client_refuses_entry_when_global_activity_is_paused(self):
        success, tx_hash, error = await LighterClient().open_snipe_order(
            side="LONG", size_btc=0.01, limit_price=100_000.0, trade_id=1,
        )

        self.assertFalse(success)
        self.assertIsNone(tx_hash)
        self.assertIn("paused", error)

    async def test_entry_queued_on_submission_lock_observes_new_pause(self):
        settings_manager._settings["trading_enabled"] = True
        client = LighterClient()
        await client._lock.acquire()
        try:
            order_task = asyncio.create_task(client.open_snipe_order(
                side="LONG", size_btc=0.01, limit_price=100_000.0, trade_id=2,
            ))
            await asyncio.sleep(0)
            settings_manager._settings["trading_enabled"] = False
        finally:
            client._lock.release()

        success, tx_hash, error = await order_task
        self.assertFalse(success)
        self.assertIsNone(tx_hash)
        self.assertIn("paused", error)


if __name__ == "__main__":
    unittest.main()
