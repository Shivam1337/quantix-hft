"""Terminal-only tests for fill-confirmed real-mode Lighter execution."""
import asyncio
import copy
import time
import unittest
from unittest.mock import patch

from app.core.lighter_order_reconciliation import (
    LighterOrderOutcome,
    order_outcome_from_api,
    wait_for_terminal_order,
)
from app.core.lighter_client import LighterClient
from app.core.settings_manager import settings_manager
from app.core.sniper_engine import SniperEngine
from app.core.wallet_manager import wallet_manager


class OrderReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def test_lighter_base_minimum_is_enforced_even_above_ten_usdc(self):
        _, _, error = LighterClient._validate_order_values(0.00009, 200000.0)

        self.assertIn("0.00010 BTC minimum", error)

    def test_terminal_order_uses_lighter_filled_amounts_and_timestamp(self):
        outcome = order_outcome_from_api({
            "client_order_index": 7,
            "status": "canceled",
            "filled_base_amount": "0.02500",
            "filled_quote_amount": "2502.50000",
            "transaction_time": "1760000000123456",
        })

        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.has_fill)
        self.assertEqual(7, outcome.client_order_index)
        self.assertEqual("canceled", outcome.status)
        self.assertEqual(0.025, outcome.filled_size_btc)
        self.assertEqual(100100.0, outcome.average_fill_price)
        self.assertEqual(1760000000.123456, outcome.exchange_timestamp)

    async def test_wait_ignores_a_stale_reused_client_order_index(self):
        now = time.time()
        responses = iter([
            [{
                "client_order_index": 12,
                "status": "filled",
                "filled_base_amount": "0.01",
                "filled_quote_amount": "1000",
                "transaction_time": int((now - 60) * 1000),
            }],
            [{
                "client_order_index": 12,
                "status": "filled",
                "filled_base_amount": "0.02",
                "filled_quote_amount": "2002",
                "transaction_time": int(now * 1000),
            }],
        ])

        async def fetch_orders():
            return next(responses)

        outcome = await wait_for_terminal_order(
            fetch_orders,
            client_order_index=12,
            timeout_seconds=0.1,
            not_before_epoch=now,
            poll_interval_seconds=0.001,
        )

        self.assertEqual(0.02, outcome.filled_size_btc)
        self.assertEqual(100100.0, outcome.average_fill_price)


class FakeLighterClient:
    def __init__(self):
        self.entry_gate = asyncio.Event()
        self.exit_gate = asyncio.Event()
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
            await self.entry_gate.wait()
            return LighterOrderOutcome(client_order_index, "canceled", 0.05, 5.005, 100.1, time.time())
        await self.exit_gate.wait()
        return LighterOrderOutcome(client_order_index, "filled", 0.05, 5.5, 110.0, time.time())


class LiveExecutionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        self.original_balances = copy.deepcopy(wallet_manager._balances)
        settings_manager._settings.update({
            "trading_mode": "REAL",
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

    async def test_exit_waits_for_entry_fill_and_records_only_confirmed_exit(self):
        fake_client = FakeLighterClient()
        engine = SniperEngine()
        with patch("app.core.lighter_client.lighter_client", fake_client):
            opened = engine.process_tick(
                self._book(), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            self.assertEqual("ENTRY_SUBMITTED", opened["active_position"]["execution_state"])
            await asyncio.sleep(0)
            self.assertEqual(1, len(fake_client.open_calls))

            pending = engine.process_tick(
                self._book(110.0), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            self.assertEqual("ENTRY_PENDING", pending["decision"]["stance"])
            self.assertEqual([], fake_client.close_calls)

            fake_client.entry_gate.set()
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertEqual("OPEN", engine.active_trade["execution_state"])
            self.assertEqual("PARTIAL", engine.active_trade["entry_fill_status"])
            self.assertEqual(0.05, engine.active_trade["size_btc"])
            self.assertEqual(110.0, engine.active_trade["target_px"])

            engine.process_tick(
                self._book(110.0), "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            self.assertEqual("EXIT_SUBMITTED", engine.active_trade["execution_state"])
            self.assertEqual(0, len(engine.closed_trades))
            await asyncio.sleep(0)
            self.assertEqual(1, len(fake_client.close_calls))
            self.assertEqual(0.05, fake_client.close_calls[0]["size_btc"])

            fake_client.exit_gate.set()
            for _ in range(3):
                await asyncio.sleep(0)

        self.assertIsNone(engine.active_trade)
        self.assertEqual(1, len(engine.closed_trades))
        closed = engine.closed_trades[0]
        self.assertEqual("EXIT_FILLED", closed["execution_state"])
        self.assertEqual(0.05, closed["size_btc"])
        self.assertAlmostEqual(0.495, closed["net_pnl"])
        self.assertIn("signal_to_submit", closed["latencies_ms"])
        self.assertIn("exit_ack_to_fill", closed["latencies_ms"])

    async def test_partial_exit_remains_open_until_all_filled_quantity_is_closed(self):
        engine = SniperEngine()
        trade = {
            "id": 3, "side": "LONG", "entry_px": 100.0, "entry_filled_size_btc": 0.05,
            "size": 0.05, "size_btc": 0.05, "leverage": 50.0, "entry_ts": time.time(),
            "latencies_ms": {}, "exit_fills": [], "exit_reason": "TARGET_REACHED",
        }
        engine.active_trade = trade
        engine._apply_exit_fill(trade, LighterOrderOutcome(10_003, "canceled", 0.02, 2.2, 110.0, time.time()))

        self.assertIs(engine.active_trade, trade)
        self.assertEqual("OPEN", trade["execution_state"])
        self.assertAlmostEqual(0.03, trade["size_btc"])
        self.assertEqual(0, len(engine.closed_trades))

        engine._apply_exit_fill(trade, LighterOrderOutcome(10_003, "filled", 0.03, 3.3, 110.0, time.time()))
        self.assertIsNone(engine.active_trade)
        self.assertAlmostEqual(0.5, engine.closed_trades[0]["net_pnl"])


class SimulationProfitRegressionTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        settings_manager._settings.update({"trading_mode": "SIMULATION", "min_lag_trigger": 6.0})

    def tearDown(self):
        settings_manager._settings = self.original_settings

    def test_simulation_target_and_pnl_logic_are_unchanged(self):
        engine = SniperEngine()
        state = LiveExecutionLifecycleTests._book()
        engine.process_tick(state, "Binance", 110.0, 110.0, 10.0, "HIGH_CONVICTION", "Major venues agree.")
        planned = copy.deepcopy(engine.active_trade)
        engine.process_tick(
            LiveExecutionLifecycleTests._book(110.0), "Binance", 110.0, 110.0, 10.0,
            "HIGH_CONVICTION", "Major venues agree.",
        )

        closed = engine.closed_trades[0]
        self.assertEqual(110.0, planned["target_px"])
        self.assertAlmostEqual((110.0 - planned["entry_px"]) * planned["size"], closed["net_pnl"])


if __name__ == "__main__":
    unittest.main()
