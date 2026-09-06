"""Engine integration tests for liquidity-capped sniper entries."""
import copy
import unittest

from app.core.settings_manager import settings_manager
from app.core.sniper_engine import SniperEngine
from app.core.state_manager import StateManager


class NoopPersistence:
    """Enough of the derived-store contract for in-memory market-state tests."""

    def record_chart_sample(self, _sample):
        pass

    def record_trade(self, _trade):
        pass

    def record_event(self, _event):
        pass

    def record_decision(self, _decision):
        pass

    def stats(self):
        return {"backend": "test", "connected": True}


class SniperLiquidityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        settings_manager._settings.update(
            {
                "trading_mode": "SIMULATION",
                "simulation_starting_balance": 100.0,
                "trade_margin_fraction": 0.50,
                "leverage": 50.0,
                "min_lag_trigger": 6.0,
            }
        )

    def tearDown(self):
        settings_manager._settings = self.original_settings

    def test_long_entry_uses_a_profitable_three_level_ladder(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [
                    ["100.0", "0.10000"],
                    ["100.1", "0.12000"],
                    ["100.2", "0.14000"],
                    ["108.1", "3.00000"],
                ],
            },
            "Binance",
            110.0,
            110.0,
            10.0,
            "HIGH_CONVICTION",
            "Major venues agree.",
        )

        trade = result["active_position"]
        self.assertIsNotNone(trade)
        self.assertAlmostEqual(0.18, trade["size_btc"])
        self.assertAlmostEqual(18.02, trade["notional_usd"])
        self.assertEqual(2500.0, trade["requested_notional_usd"])
        self.assertEqual(100.2, trade["execution_price_limit"])
        self.assertEqual(100.2, trade["ladder_price_limit"])
        self.assertEqual(108.8, trade["profitability_limit_price"])
        self.assertEqual(3, trade["book_levels_used"])
        self.assertAlmostEqual(100.11111111, trade["entry_px"])

    def test_short_entry_uses_a_profitable_three_level_ladder(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {
                "best_bid": 100.0,
                "best_ask": 100.1,
                "mid_price": 100.05,
                "bids": [
                    ["100.0", "0.10000"],
                    ["99.9", "0.12000"],
                    ["99.8", "0.14000"],
                    ["91.9", "3.00000"],
                ],
                "asks": [["100.1", "3.0"]],
            },
            "Binance",
            90.0,
            90.0,
            -10.0,
            "HIGH_CONVICTION",
            "Major venues agree.",
        )

        trade = result["active_position"]
        self.assertIsNotNone(trade)
        self.assertEqual("SHORT", trade["side"])
        self.assertAlmostEqual(0.18, trade["size_btc"])
        self.assertAlmostEqual(17.98, trade["notional_usd"])
        self.assertEqual(99.8, trade["execution_price_limit"])
        self.assertEqual(99.8, trade["ladder_price_limit"])
        self.assertEqual(91.2, trade["profitability_limit_price"])
        self.assertEqual(3, trade["book_levels_used"])
        self.assertAlmostEqual(99.88888889, trade["entry_px"])

    def test_signal_rejects_an_order_at_the_post_haircut_ten_usdc_floor(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [["100.0", "0.20000"]],
            },
            "Binance",
            110.0,
            110.0,
            10.0,
            "HIGH_CONVICTION",
            "Major venues agree.",
        )

        self.assertIsNone(result["active_position"])
        self.assertEqual("BELOW_MINIMUM_NOTIONAL", result["decision"]["rejection_reason"])
        self.assertEqual(0, engine.trade_counter)

    def test_signal_does_not_use_unprofitable_depth_to_reach_the_minimum(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [["100.0", "0.05000"], ["109.0", "3.00000"]],
            },
            "Binance",
            110.0,
            110.0,
            10.0,
            "HIGH_CONVICTION",
            "Major venues agree.",
        )

        decision = result["decision"]
        self.assertIsNone(result["active_position"])
        self.assertEqual("BELOW_MINIMUM_NOTIONAL", decision["rejection_reason"])
        self.assertEqual(108.0, decision["profitability_limit_price"])
        self.assertAlmostEqual(0.05, decision["visible_liquidity_btc"])
        self.assertEqual(0, engine.trade_counter)

    def test_lighter_book_is_cleared_before_a_reconnect_can_reuse_old_sizes(self):
        manager = StateManager(persistence=NoopPersistence())
        manager.update_lighter(
            [["99.9", "1.0"]],
            [["100.0", "2.0"]],
            99.9,
            100.0,
        )

        manager.reset_lighter_orderbook(status="WS RECONNECTING...")

        self.assertEqual([], manager.lighter["bids"])
        self.assertEqual([], manager.lighter["asks"])
        self.assertEqual(0.0, manager.lighter["best_bid"])
        self.assertEqual(0.0, manager.lighter["best_ask"])
        self.assertIsNone(manager.lighter["last_update_monotonic_ns"])


if __name__ == "__main__":
    unittest.main()
