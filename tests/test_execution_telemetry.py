"""Terminal-only coverage for persisted live-order execution telemetry."""
import asyncio
import copy
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app.core.execution.telemetry import (
    MAX_EXECUTION_ATTEMPTS_HISTORY,
    ExecutionTelemetryMixin,
    capture_lighter_book,
)
from app.core.lighter_order_reconciliation import LighterOrderOutcome
from app.core.settings_manager import settings_manager
from app.core.sniper_engine import SniperEngine
from app.core.sqlite_store import SqliteStore
from app.core.wallet_manager import wallet_manager


class BookTelemetryTests(unittest.TestCase):
    def test_book_snapshot_preserves_sequence_depth_and_age(self):
        received = time.monotonic_ns() - 12_345_000
        snapshot = capture_lighter_book({
            "last_update_monotonic_ns": received,
            "last_update_utc": "2026-09-06T02:00:00+00:00",
            "exchange_timestamp_ms": 1_788_000_000_123,
            "source_sequence": "991",
            "best_bid": 100.0,
            "best_ask": 100.1,
            "spread": 0.1,
            "bids": [["100.0", "0.2"], ["99.9", "0.3"]],
            "asks": [["100.1", "0.4"], ["100.2", "0.5"]],
        }, captured_epoch=1_788_000_000.0, captured_monotonic_ns=received + 12_345_000)

        self.assertEqual(12.345, snapshot["book_age_ms"])
        self.assertEqual("991", snapshot["source_sequence"])
        self.assertEqual(100.0, snapshot["top_bids"][0]["price"])
        self.assertEqual(0.4, snapshot["top_asks"][0]["size"])

    def test_restore_keeps_newest_bounded_attempts(self):
        telemetry = ExecutionTelemetryMixin()
        telemetry._init_execution_telemetry()
        attempts = [
            {"attempt_id": str(index)}
            for index in range(MAX_EXECUTION_ATTEMPTS_HISTORY + 5)
        ]

        telemetry.hydrate_execution_attempts(attempts)

        restored = telemetry.get_execution_attempts()
        self.assertEqual(MAX_EXECUTION_ATTEMPTS_HISTORY, len(restored))
        self.assertEqual("0", restored[0]["attempt_id"])
        self.assertEqual(str(MAX_EXECUTION_ATTEMPTS_HISTORY - 1), restored[-1]["attempt_id"])


class CanceledEntryTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = copy.deepcopy(settings_manager._settings)
        self.original_balances = copy.deepcopy(wallet_manager._balances)
        settings_manager._settings.update({
            "trading_mode": "REAL", "trading_enabled": True,
            "trade_margin_fraction": 0.50, "leverage": 50.0, "min_lag_trigger": 6.0,
        })
        wallet_manager._balances.update({
            "lighter_account_data_available": True,
            "lighter_account_equity_usd": 100.0,
            "lighter_free_margin_usd": 100.0,
        })

    def tearDown(self):
        settings_manager._settings = self.original_settings
        wallet_manager._balances = self.original_balances

    async def test_canceled_entry_retains_book_and_end_to_end_timing(self):
        current_book = self._book()
        received = time.monotonic_ns() - 4_000_000
        current_book.update({
            "last_update_monotonic_ns": received,
            "last_update_utc": "2026-09-06T02:00:00+00:00",
            "exchange_timestamp_ms": 1_788_000_000_123,
            "source_sequence": "500",
        })
        persisted = []
        engine = SniperEngine()
        engine.configure_execution_telemetry(
            book_snapshot_provider=lambda: current_book,
            attempt_sink=persisted.append,
        )

        with patch("app.core.lighter_client.lighter_client", _CanceledEntryClient()):
            engine.process_tick(
                current_book, "Binance", 110.0, 110.0, 10.0,
                "HIGH_CONVICTION", "Major venues agree.",
            )
            for _ in range(3):
                await asyncio.sleep(0)

        self.assertIsNone(engine.active_trade)
        self.assertEqual(1, len(persisted))
        attempt = engine.get_execution_attempts()[0]
        self.assertEqual("ENTRY_NOT_FILLED", attempt["result"])
        self.assertEqual("canceled-too-much-slippage", attempt["order"]["terminal"]["exchange_status"])
        self.assertEqual("500", attempt["signal"]["lighter_book"]["source_sequence"])
        self.assertEqual(99.9, attempt["order"]["lighter_book"]["best_bid"])
        self.assertGreaterEqual(attempt["signal"]["lighter_book"]["book_age_ms"], 0.0)
        self.assertIn("signal_to_submit", attempt["latencies_ms"])
        self.assertIn("entry_ack_to_terminal_observed", attempt["latencies_ms"])

    @staticmethod
    def _book():
        return {
            "best_bid": 99.9, "best_ask": 100.0, "mid_price": 99.95,
            "bids": [["99.9", "3.0"], ["99.8", "3.0"]],
            "asks": [["100.0", "0.05"], ["100.1", "0.06"], ["100.2", "0.07"]],
        }


class _CanceledEntryClient:
    async def open_snipe_order(self, **_kwargs):
        return True, "entry-transaction", None

    async def wait_for_order_outcome(self, *, client_order_index, **_kwargs):
        return LighterOrderOutcome(
            client_order_index, "canceled-too-much-slippage", 0.0, 0.0, None, time.time(),
        )


class PersistedAttemptTests(unittest.TestCase):
    def test_execution_attempt_is_reloaded_from_sqlite_event_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "telemetry.db")
            asyncio.run(self._store_and_reload(db_path))

    async def _store_and_reload(self, db_path):
        store = SqliteStore(db_path=db_path)
        await store.start(chart_limit=5, trade_limit=5, event_limit=5)
        store.record_event({
            "transition": "EXECUTION_ATTEMPT",
            "event": {"attempt_id": "17:ENTRY:1", "result": "ENTRY_NOT_FILLED"},
        })
        await store.stop()

        reloaded = SqliteStore(db_path=db_path)
        snapshot = await reloaded.start(chart_limit=5, trade_limit=5, event_limit=5)
        self.assertEqual("17:ENTRY:1", snapshot["execution_attempts"][0]["attempt_id"])
        await reloaded.stop()


if __name__ == "__main__":
    unittest.main()
