"""Tests for displayed-depth caps and minimum-notional execution safeguards."""
import copy
import unittest

from app.core.execution import calculate_executable_order, calculate_profitable_price_limit
from app.core.lighter_client import LighterClient
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


class VisibleLiquiditySizingTests(unittest.TestCase):
    def test_long_uses_only_asks_at_or_below_the_displayed_limit(self):
        order = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100.0", "0.20000"], ["100.1", "9.00000"]],
            limit_price=100.0,
            notional_cap_usd=2500.0,
        )

        self.assertEqual(0.2, order.size_btc)
        self.assertEqual(20.0, order.notional_usd)
        self.assertEqual(1, order.levels_used)
        self.assertTrue(order.meets_minimums)

    def test_ladder_uses_three_levels_and_uses_the_deepest_used_price_as_ioc_limit(self):
        order = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[
                ["100.0", "0.05000"],
                ["100.1", "0.06000"],
                ["100.2", "0.07000"],
                ["100.3", "5.00000"],
            ],
            limit_price=100.2,
            notional_cap_usd=2500.0,
            max_levels=3,
        )

        self.assertAlmostEqual(0.18, order.size_btc)
        self.assertAlmostEqual(18.02, order.notional_usd)
        self.assertAlmostEqual(100.11111111, order.vwap_price)
        self.assertEqual(100.2, order.limit_price)
        self.assertEqual(100.2, order.profitability_limit_price)
        self.assertAlmostEqual(18.036, order.worst_case_notional_usd)
        self.assertEqual(3, order.levels_used)

    def test_ladder_caps_quantity_at_the_deepest_price_before_submitting_ioc(self):
        order = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100.0", "0.10000"], ["102.0", "0.20000"]],
            limit_price=102.0,
            notional_cap_usd=20.0,
            max_levels=2,
        )

        self.assertAlmostEqual(0.19607, order.size_btc)
        self.assertAlmostEqual(19.79914, order.notional_usd)
        self.assertAlmostEqual(19.99914, order.limit_notional_usd)
        self.assertLessEqual(order.worst_case_notional_usd, 20.0)
        self.assertEqual(102.0, order.limit_price)

    def test_short_ladder_requires_the_ioc_bound_to_clear_the_strict_ten_usdc_floor(self):
        order = calculate_executable_order(
            side="SHORT",
            bids=[["101.0", "0.10000"], ["99.0", "0.00001"]],
            asks=[],
            limit_price=99.0,
            notional_cap_usd=100.0,
            max_levels=2,
        )

        self.assertGreater(order.notional_usd, 10.0)
        self.assertLess(order.limit_notional_usd, 10.0)
        self.assertFalse(order.meets_minimums)

    def test_profitability_limits_leave_expected_profit_after_the_exit_threshold(self):
        self.assertEqual(
            108.0,
            calculate_profitable_price_limit(
                side="LONG",
                target_price=110.04,
                target_exit_buffer_usd=1.0,
                minimum_expected_profit_usd=1.0,
            ),
        )
        self.assertEqual(
            92.1,
            calculate_profitable_price_limit(
                side="SHORT",
                target_price=90.04,
                target_exit_buffer_usd=1.0,
                minimum_expected_profit_usd=1.0,
            ),
        )

    def test_short_caps_size_by_visible_bid_and_notional_cap(self):
        order = calculate_executable_order(
            side="SHORT",
            bids=[["101.0", "1.00000"], ["100.9", "9.00000"]],
            asks=[],
            limit_price=101.0,
            notional_cap_usd=20.0,
        )

        self.assertEqual(0.19801, order.size_btc)
        self.assertLessEqual(order.notional_usd, 20.0)
        self.assertEqual(20.0 - 0.00099, order.notional_usd)
        self.assertEqual(1, order.levels_used)

    def test_exactly_ten_usdc_is_rejected_but_amount_above_it_is_valid(self):
        at_floor = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100", "0.10000"]],
            limit_price=100.0,
            notional_cap_usd=100.0,
        )
        above_floor = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100", "0.10001"]],
            limit_price=100.0,
            notional_cap_usd=100.0,
        )

        self.assertEqual(10.0, at_floor.notional_usd)
        self.assertFalse(at_floor.meets_minimums)
        self.assertGreater(above_floor.notional_usd, 10.0)
        self.assertTrue(above_floor.meets_minimums)


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
                    ["100.0", "0.05000"],
                    ["100.1", "0.06000"],
                    ["100.2", "0.07000"],
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
        self.assertEqual(108.0, trade["profitability_limit_price"])
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
                    ["100.0", "0.05000"],
                    ["99.9", "0.06000"],
                    ["99.8", "0.07000"],
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
        self.assertEqual(92.0, trade["profitability_limit_price"])
        self.assertEqual(3, trade["book_levels_used"])
        self.assertAlmostEqual(99.88888889, trade["entry_px"])

    def test_signal_does_not_create_an_order_at_the_ten_usdc_floor(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {
                "best_bid": 99.9,
                "best_ask": 100.0,
                "mid_price": 99.95,
                "bids": [["99.9", "3.0"]],
                "asks": [["100.0", "0.10000"]],
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
                "asks": [["100.0", "0.05000"], ["108.1", "3.00000"]],
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


class FakeResponse:
    tx_hash = "test-tx"


class FakeSigner:
    ORDER_TYPE_MARKET = 1
    ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL = 0
    DEFAULT_IOC_EXPIRY = -1

    def __init__(self):
        self.order = None

    async def create_order(self, **kwargs):
        self.order = kwargs
        return object(), FakeResponse(), None


class LighterOrderGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_client_uses_the_exact_displayed_limit_and_floor_quantity(self):
        client = LighterClient()
        signer = FakeSigner()
        client._get_signer = lambda: signer

        success, tx_hash, error = await client.open_snipe_order(
            side="LONG",
            size_btc=0.100019,
            limit_price=100.0,
            trade_id=7,
        )

        self.assertTrue(success)
        self.assertEqual("test-tx", tx_hash)
        self.assertIsNone(error)
        self.assertEqual(10_001, signer.order["base_amount"])
        self.assertEqual(1_000, signer.order["price"])
        self.assertFalse(signer.order["is_ask"])

    async def test_live_client_rejects_an_order_at_or_below_ten_usdc(self):
        client = LighterClient()
        signer = FakeSigner()
        client._get_signer = lambda: signer

        success, tx_hash, error = await client.open_snipe_order(
            side="LONG",
            size_btc=0.1,
            limit_price=100.0,
            trade_id=8,
        )

        self.assertFalse(success)
        self.assertIsNone(tx_hash)
        self.assertIn("strictly greater", error)
        self.assertIsNone(signer.order)


if __name__ == "__main__":
    unittest.main()
