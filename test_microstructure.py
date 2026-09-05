"""
Unit tests for Microstructure Market Making Engine and Signals
"""

import unittest
from signals import BookLevel, OrderBook, calculate_ofi, AvellanedaStoikovModel, RollingVolatility
from engine import HFMarketMakingSimulator


class TestMicrostructure(unittest.TestCase):

    def setUp(self):
        self.bids = [BookLevel(price=100.0, size=10.0), BookLevel(price=99.9, size=20.0)]
        self.asks = [BookLevel(price=100.2, size=10.0), BookLevel(price=100.3, size=20.0)]
        self.book = OrderBook(bids=self.bids, asks=self.asks, timestamp=1.0)

    def test_order_book_metrics(self):
        self.assertEqual(self.book.best_bid, 100.0)
        self.assertEqual(self.book.best_ask, 100.2)
        self.assertAlmostEqual(self.book.mid_price, 100.1)
        self.assertAlmostEqual(self.book.spread, 0.2)
        # Micro price with equal depth should equal mid price
        self.assertAlmostEqual(self.book.micro_price, 100.1)

    def test_ofi_calculation(self):
        # New book with bid building from 10 to 15 (buying pressure)
        new_bids = [BookLevel(price=100.0, size=15.0), BookLevel(price=99.9, size=20.0)]
        new_asks = [BookLevel(price=100.2, size=10.0), BookLevel(price=100.3, size=20.0)]
        new_book = OrderBook(bids=new_bids, asks=new_asks, timestamp=1.1)

        ofi = calculate_ofi(self.book, new_book, levels=1)
        self.assertGreater(ofi, 0, "OFI should be positive when bid depth increases")

        # New book with ask building from 10 to 18 (selling pressure)
        new_asks2 = [BookLevel(price=100.2, size=18.0), BookLevel(price=100.3, size=20.0)]
        new_book2 = OrderBook(bids=self.bids, asks=new_asks2, timestamp=1.2)
        ofi2 = calculate_ofi(self.book, new_book2, levels=1)
        self.assertLess(ofi2, 0, "OFI should be negative when ask depth increases")

    def test_as_reservation_price_inventory_skew(self):
        model = AvellanedaStoikovModel(gamma=0.2, kappa=1.5, beta_ofi=0.001)
        mid = 100.0
        vol = 0.01

        # Neutral inventory
        r_neutral = model.compute_reservation_price(mid, inventory=0.0, volatility=vol)
        self.assertAlmostEqual(r_neutral, 100.0)

        # Long inventory (q > 0) should lower reservation price to offload inventory
        r_long = model.compute_reservation_price(mid, inventory=5.0, volatility=vol)
        self.assertLess(r_long, r_neutral)

        # Short inventory (q < 0) should raise reservation price to buy back
        r_short = model.compute_reservation_price(mid, inventory=-5.0, volatility=vol)
        self.assertGreater(r_short, r_neutral)

        # Positive OFI should skew reservation price upwards
        r_ofi = model.compute_reservation_price(mid, inventory=0.0, volatility=vol, ofi=10.0)
        self.assertGreater(r_ofi, r_neutral)

    def test_simulator_fill_execution(self):
        model = AvellanedaStoikovModel(tick_size=0.01)
        sim = HFMarketMakingSimulator(
            strategy_name="TestAS",
            model=model,
            initial_capital=1000.0,
            order_size_notional=100.0,
            latency_ms=0.0 # Instant for unit test
        )

        sim.on_book_update(self.book)
        self.assertIsNotNone(sim.pending_quote)
        sim.on_book_update(self.book) # Activates quote
        self.assertIsNotNone(sim.active_quote)

        active_ask = sim.active_quote.ask_price
        # Simulate a taker buy trade that crosses our ask
        sim.on_trade(timestamp=2.0, price=active_ask + 0.05, size=2.0, side='B')

        # We should have a SELL fill
        self.assertEqual(len(sim.fills), 1)
        self.assertEqual(sim.fills[0].side, 'SELL')
        self.assertLess(sim.inventory, 0.0)
        self.assertGreater(sim.cash, 1000.0)


if __name__ == '__main__':
    unittest.main()
