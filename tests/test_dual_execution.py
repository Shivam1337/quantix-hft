"""Terminal-only coverage for matched simulated and real DUAL executions."""
import asyncio
import copy
import time
import unittest
from unittest.mock import patch

from app.core.lighter_order_reconciliation import LighterOrderOutcome
from app.core.settings_manager import SettingsManager, settings_manager
from app.core.sniper_engine import SniperEngine
from app.core.state_manager import StateManager
from app.core.wallet_manager import wallet_manager


class _FilledClient:
    def __init__(self):
        self.open_calls = []
        self.close_calls = []

    async def open_snipe_order(self, **kwargs):
        self.open_calls.append(kwargs)
        return True, "entry-tx", None

    async def close_snipe_order(self, **kwargs):
        self.close_calls.append(kwargs)
        return True, "exit-tx", None

    async def wait_for_order_outcome(self, *, client_order_index, **_kwargs):
        if client_order_index < 10_000:
            return LighterOrderOutcome(client_order_index, "filled", 0.05, 5.015, 100.3, time.time())
        return LighterOrderOutcome(client_order_index, "filled", 0.05, 5.495, 109.9, time.time())


class _FailingEntryClient:
    async def open_snipe_order(self, **_kwargs):
        return False, None, "sequencer unavailable"


class DualExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        self.original_balances = copy.deepcopy(wallet_manager._balances)
        settings_manager._settings.update({
            "trading_mode": "DUAL",
            "account_index": 7,
            "api_private_key": "0x0123456789abcdef",
            "simulation_starting_balance": 100.0,
            "trade_margin_fraction": 0.50,
            "leverage": 50.0,
            "min_lag_trigger": 6.0,
        })
        wallet_manager._balances.update({
            "lighter_account_data_available": True,
            "lighter_account_equity_usd": 100.0,
            "lighter_collateral_usd": 100.0,
            "lighter_free_margin_usd": 100.0,
            "lighter_margin_used_usd": 0.0,
        })

    def tearDown(self):
        settings_manager._settings = self.original_settings
        wallet_manager._balances = self.original_balances

    @staticmethod
    def _book(best_bid=99.9):
        return {
            "best_bid": best_bid,
            "best_ask": 100.0 if best_bid < 105 else 110.1,
            "mid_price": (best_bid + (100.0 if best_bid < 105 else 110.1)) / 2,
            "bids": [[str(best_bid), "3.0"]],
            "asks": [["100.0", "0.10000"], ["100.1", "0.12000"], ["100.2", "0.14000"]],
        }

    async def _drain_tasks(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_dual_mode_records_same_signal_simulation_and_confirmed_real_fill(self):
        client = _FilledClient()
        engine = SniperEngine()
        with patch("app.core.lighter_client.lighter_client", client):
            opened = engine.process_tick(
                self._book(), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            self.assertEqual("DUAL", opened["trading_mode"])
            self.assertTrue(opened["active_position"]["dual_execution"])
            self.assertEqual("LIVE_ENTRY_SUBMITTED", opened["execution_comparisons"][0]["status"])
            await self._drain_tasks()

            self.assertEqual("OPEN", engine.active_trade["execution_state"])
            self.assertEqual("OPEN", engine.get_execution_comparisons()[0]["status"])
            self.assertEqual(1, len(client.open_calls))

            engine.process_tick(
                self._book(110.0), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            await self._drain_tasks()

        comparison = engine.get_execution_comparisons()[0]
        closed = engine.closed_trades[0]
        self.assertIsNone(engine.active_trade)
        self.assertEqual("COMPLETE", comparison["status"])
        self.assertEqual("SIMULATED_CLOSED", comparison["simulated"]["status"])
        self.assertEqual("CLOSED", comparison["real"]["status"])
        self.assertEqual(1, len(client.close_calls))
        self.assertAlmostEqual(
            comparison["real"]["net_pnl"] - comparison["simulated"]["net_pnl"],
            comparison["pnl_delta_usd"],
        )
        self.assertEqual("COMPLETE", closed["execution_comparison"]["status"])
        performance = engine.get_performance()
        self.assertTrue(performance["is_real_mode"])
        self.assertEqual("CONFIRMED_REAL_STRATEGY_WITH_SIMULATION_CONTROL", performance["metrics_scope"])
        self.assertEqual(1, performance["total_trades"])

    async def test_dual_mode_keeps_the_simulated_control_when_live_entry_fails(self):
        engine = SniperEngine()
        with patch("app.core.lighter_client.lighter_client", _FailingEntryClient()):
            engine.process_tick(
                self._book(), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            await self._drain_tasks()

        comparison = engine.get_execution_comparisons()[0]
        self.assertIsNone(engine.active_trade)
        self.assertEqual("LIVE_ENTRY_SUBMISSION_FAILED", comparison["status"])
        self.assertEqual("SIMULATED_ONLY", comparison["simulated"]["status"])
        self.assertEqual("sequencer unavailable", comparison["real"]["error"])

    async def test_simulation_reset_never_clears_an_active_dual_live_position(self):
        class _Persistence:
            def __init__(self):
                self.reset_calls = 0

            async def reset_simulation_data(self):
                self.reset_calls += 1

            def record_event(self, _event):
                pass

        persistence = _Persistence()
        manager = StateManager(persistence=persistence)
        manager.sniper_engine.active_trade = {"mode": "REAL", "dual_execution": True}

        result = await manager.reset_simulation()

        self.assertEqual("blocked", result["status"])
        self.assertEqual(0, persistence.reset_calls)
        self.assertIsNotNone(manager.sniper_engine.active_trade)


class DualSettingsTests(unittest.TestCase):
    def test_dual_mode_requires_real_credentials_and_enables_real_order_routing(self):
        manager = SettingsManager(db_path=":memory:")
        failed, message = manager.set_trading_mode("DUAL")
        self.assertFalse(failed)
        self.assertIn("Account Index", message)

        success, _ = manager.update_settings({
            "trading_mode": "DUAL",
            "account_index": 7,
            "api_private_key": "0x0123456789abcdef",
        })
        self.assertTrue(success)
        self.assertEqual("DUAL", manager.trading_mode)
        self.assertTrue(manager.is_real_mode)
        self.assertTrue(manager.is_dual_mode)


if __name__ == "__main__":
    unittest.main()
