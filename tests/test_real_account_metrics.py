"""Tests for real-mode account snapshots and performance isolation."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from app.core import real_account_refresh
from app.core.lighter_account import parse_lighter_account_response
from app.core.settings_manager import settings_manager
from app.core.sniper_engine import SniperEngine
from app.core.wallet_manager import wallet_manager


class LighterAccountSnapshotTests(unittest.TestCase):
    def test_current_lighter_accounts_response_exposes_equity_and_free_margin(self):
        snapshot = parse_lighter_account_response({
            "accounts": [{
                "account_index": 77,
                "status": 1,
                "collateral": "1000.50",
                "available_balance": "700.25",
                "transaction_time": 1760000000123456,
                "positions": [
                    {
                        "market_id": 1,
                        "symbol": "BTC",
                        "sign": -1,
                        "position": "0.01000",
                        "position_value": "-800.00",
                        "unrealized_pnl": "4.50",
                        "realized_pnl": "1.25",
                    },
                    {
                        "market_id": 2,
                        "symbol": "SOL",
                        "sign": 1,
                        "position": "2.0",
                        "position_value": "200.00",
                        "unrealized_pnl": "-1.00",
                        "realized_pnl": "0.75",
                    },
                ],
            }],
        })

        self.assertTrue(snapshot["lighter_account_data_available"])
        self.assertEqual("ACTIVE", snapshot["lighter_account_status"])
        self.assertEqual(1004.0, snapshot["lighter_account_equity_usd"])
        self.assertEqual(700.25, snapshot["lighter_free_margin_usd"])
        self.assertEqual(300.25, snapshot["lighter_margin_used_usd"])
        self.assertEqual(1000.0, snapshot["lighter_position_notional_usd"])
        self.assertEqual(3.5, snapshot["lighter_unrealized_pnl_usd"])
        self.assertEqual(-0.01, snapshot["lighter_btc_position_btc"])


class RealPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        self.original_balances = copy.deepcopy(wallet_manager._balances)
        settings_manager._settings.update({
            "trading_mode": "REAL",
            "trade_margin_fraction": 0.50,
            "leverage": 50.0,
        })
        wallet_manager._balances = {
            "lighter_account_data_available": True,
            "lighter_account_equity_usd": 1004.0,
            "lighter_collateral_usd": 1000.50,
            "lighter_free_margin_usd": 700.25,
            "lighter_margin_used_usd": 300.25,
            "lighter_position_notional_usd": 1000.0,
            "lighter_unrealized_pnl_usd": 3.50,
        }

    def tearDown(self):
        settings_manager._settings = self.original_settings
        wallet_manager._balances = self.original_balances

    def test_real_metrics_use_exchange_equity_and_only_confirmed_real_trades(self):
        engine = SniperEngine()
        engine.closed_trades.extend([
            {"mode": "SIMULATION", "net_pnl": 999.0, "gross_pnl": 999.0, "is_win": True, "hold_sec": 1.0},
            {
                "mode": "REAL", "net_pnl": 12.0, "gross_pnl": 12.0, "is_win": True,
                "hold_sec": 0.2, "notional_usd": 1000.0, "size_btc": 0.01, "exit_px": 110000.0,
            },
            {
                "mode": "REAL", "net_pnl": -2.0, "gross_pnl": -2.0, "is_win": False,
                "hold_sec": 0.3, "notional_usd": 500.0, "size_btc": 0.005, "exit_px": 90000.0,
            },
        ])

        performance = engine.get_performance()

        self.assertTrue(performance["account_data_available"])
        self.assertEqual("CONFIRMED_REAL_STRATEGY", performance["metrics_scope"])
        self.assertEqual(1004.0, performance["account_equity_usd"])
        self.assertEqual(700.25, performance["free_margin_usd"])
        self.assertEqual(300.25, performance["margin_used_usd"])
        self.assertEqual(502.0, performance["target_margin_usd"])
        self.assertEqual(25100.0, performance["target_notional_usd"])
        self.assertEqual(2, performance["total_trades"])
        self.assertEqual(10.0, performance["net_pnl"])
        self.assertEqual(50.0, performance["win_rate"])
        self.assertEqual(1.22, performance["fees_saved_vs_poly"])

    def test_real_metrics_never_substitute_simulation_equity_when_snapshot_is_missing(self):
        wallet_manager._balances = {"lighter_account_data_available": False}

        performance = SniperEngine().get_performance()

        self.assertFalse(performance["account_data_available"])
        self.assertEqual(0.0, performance["account_equity_usd"])
        self.assertEqual(0.0, performance["target_notional_usd"])

    def test_real_entry_waits_for_verified_account_equity_instead_of_simulation_balance(self):
        wallet_manager._balances = {"lighter_account_data_available": False}
        result = SniperEngine().process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [["100.0", "1.0"]],
            },
            "Binance", 110.0, 110.0, 10.0, "HIGH_CONVICTION", "Major venues agree.",
        )

        self.assertIsNone(result["active_position"])
        self.assertEqual("REAL_ACCOUNT_SNAPSHOT_UNAVAILABLE", result["decision"]["rejection_reason"])

    def test_real_size_uses_only_exchange_free_margin_and_never_simulation_equity(self):
        wallet_manager._balances = {
            "lighter_account_data_available": True,
            "lighter_account_equity_usd": 1_000.0,
            "lighter_collateral_usd": 1_000.0,
            "lighter_free_margin_usd": 25.0,
        }
        engine = SniperEngine()
        calculated = engine.calculate_trade_size(100_000.0)

        self.assertEqual(25.0, calculated["margin_allocated_usd"])
        self.assertEqual(1_250.0, calculated["notional_usd"])
        performance = engine.get_performance()
        self.assertEqual(25.0, performance["target_margin_usd"])
        self.assertEqual(500.0, performance["configured_target_margin_usd"])
        self.assertEqual(1_250.0, performance["target_notional_usd"])

        wallet_manager._balances.update({
            "lighter_account_equity_usd": 0.0,
            "lighter_collateral_usd": 0.0,
            "lighter_free_margin_usd": 0.0,
        })
        calculated = engine.calculate_trade_size(100_000.0)
        self.assertEqual(0.0, calculated["size_btc"])
        self.assertEqual(0.0, calculated["notional_usd"])

        result = engine.process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [["100.0", "1.0"]],
            },
            "Binance", 110.0, 110.0, 10.0, "HIGH_CONVICTION", "Major venues agree.",
        )

        self.assertIsNone(result["active_position"])
        self.assertEqual("REAL_FREE_MARGIN_UNAVAILABLE", result["decision"]["rejection_reason"])


class RealAccountRefreshTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        settings_manager._settings["trading_mode"] = "REAL"

    def tearDown(self):
        settings_manager._settings = self.original_settings

    async def test_real_mode_periodically_requests_an_account_snapshot(self):
        refreshed = asyncio.Event()

        async def fake_refresh():
            refreshed.set()
            return {}

        with patch.object(real_account_refresh.wallet_manager, "refresh_balances", fake_refresh):
            task = asyncio.create_task(real_account_refresh.real_account_refresh_task())
            await asyncio.wait_for(refreshed.wait(), timeout=0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
