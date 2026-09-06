"""Unit tests for pure displayed-depth and minimum-notional sizing."""
import unittest

from app.core.execution import calculate_executable_order, calculate_profitable_price_limit


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

    def test_fifty_percent_haircut_uses_half_of_each_profitable_level(self):
        order = calculate_executable_order(
            side="SHORT",
            bids=[
                ["79861.1", "0.00021"],
                ["79861.0", "0.00020"],
                ["79860.9", "0.00020"],
            ],
            asks=[],
            limit_price=79860.9,
            notional_cap_usd=500.0,
            max_levels=3,
            liquidity_participation=0.50,
        )

        self.assertEqual(0.00061, order.visible_size_btc)
        self.assertEqual(0.0003, order.size_btc)
        self.assertEqual(79860.9, order.limit_price)
        self.assertTrue(order.meets_minimums)

    def test_fifty_percent_haircut_also_halves_the_notional_cap(self):
        full_size = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100.0", "1.00000"]],
            limit_price=100.0,
            notional_cap_usd=40.0,
        )
        haircut_size = calculate_executable_order(
            side="LONG",
            bids=[],
            asks=[["100.0", "1.00000"]],
            limit_price=100.0,
            notional_cap_usd=40.0,
            liquidity_participation=0.50,
        )

        self.assertEqual(0.4, full_size.size_btc)
        self.assertEqual(0.2, haircut_size.size_btc)
        self.assertEqual(20.0, haircut_size.limit_notional_usd)

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


if __name__ == "__main__":
    unittest.main()
