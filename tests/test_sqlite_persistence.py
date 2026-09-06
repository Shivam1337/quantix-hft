import asyncio
import os
import tempfile
import unittest

from app.core.postgres_store import PostgresStore
from app.core.sqlite_store import SqliteStore
from app.core.state_manager import StateManager


class SqlitePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_lead_lag.db")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_sqlite_store_lifecycle_and_writes(self):
        async def run():
            store = SqliteStore(db_path=self.db_path)
            snapshot = await store.start(chart_limit=10, trade_limit=10, event_limit=10)
            self.assertEqual([], snapshot["chart_samples"])
            self.assertEqual([], snapshot["closed_trades"])
            self.assertEqual([], snapshot["repricing_events"])

            # Record samples
            store.record_chart_sample({"time": "2026-09-06T00:00:00Z", "binance": 50000.0})
            store.record_trade({
                "id": 1,
                "time": "00:00:01",
                "side": "LONG",
                "size_btc": 0.05,
                "entry_px": 50000.0,
                "exit_px": 50010.0,
                "net_pnl": 0.5,
                "is_win": True,
            })
            store.record_event({
                "transition": "SPREAD_CLOSED",
                "event": {
                    "id": 1,
                    "direction": "UPWARD_CATCHUP",
                    "spread_closed": True,
                },
            })
            store.record_decision({
                "stance": "SIGNAL_DETECTED",
                "action": "SNIPE_LONG",
            })

            await store.stop()
            self.assertEqual(4, store.records_written)
            self.assertEqual(0, store.records_failed)

            # Test hydration on new store instance
            store2 = SqliteStore(db_path=self.db_path)
            snapshot2 = await store2.start(chart_limit=10, trade_limit=10, event_limit=10)
            self.assertEqual(1, len(snapshot2["chart_samples"]))
            self.assertEqual(50000.0, snapshot2["chart_samples"][0]["binance"])
            self.assertEqual(1, len(snapshot2["closed_trades"]))
            self.assertEqual(1, snapshot2["closed_trades"][0]["id"])
            self.assertEqual(1, len(snapshot2["repricing_events"]))
            self.assertTrue(snapshot2["repricing_events"][0]["spread_closed"])
            await store2.stop()

        asyncio.run(run())

    def test_sqlite_chart_pruning(self):
        async def run():
            # Small retention of 5 items
            store = SqliteStore(db_path=self.db_path, chart_retention=5)
            await store.start(chart_limit=10, trade_limit=10, event_limit=10)
            # Insert 260 chart samples to trigger pruning (prune happens every 250 writes)
            for i in range(260):
                store.record_chart_sample({"time": f"t_{i}", "val": i})
            await store.stop()

            # Verify only retained samples remain
            store2 = SqliteStore(db_path=self.db_path)
            snapshot = await store2.start(chart_limit=100, trade_limit=10, event_limit=10)
            self.assertLessEqual(len(snapshot["chart_samples"]), 15)
            await store2.stop()

        asyncio.run(run())

    def test_postgres_store_auto_fallback_to_sqlite(self):
        async def run():
            # Connect to invalid postgres port with fallback enabled
            store = PostgresStore(
                dsn="postgresql://invalid_user:invalid_pass@127.0.0.1:59999/invalid_db",
                fallback_to_sqlite=True,
                sqlite_path=self.db_path,
            )
            snapshot = await store.start(chart_limit=10, trade_limit=10, event_limit=10)
            stats = store.stats()

            self.assertEqual("sqlite", stats["backend"])
            self.assertTrue(stats["connected"])
            self.assertTrue(stats["fallback_active"])
            self.assertIn("postgres_error", stats)
            self.assertEqual("SQLite (dev)", store.backend_name)

            store.record_chart_sample({"time": "2026-09-06T00:00:00Z", "binance": 100.0})
            await store.stop()
            self.assertEqual(1, store.stats()["records_written"])

        asyncio.run(run())


    def test_postgres_store_explicit_sqlite_dsn(self):
        async def run():
            store = PostgresStore(dsn=f"sqlite:///{self.db_path}")
            await store.start(chart_limit=10, trade_limit=10, event_limit=10)
            stats = store.stats()

            self.assertEqual("sqlite", stats["backend"])
            self.assertTrue(stats["connected"])
            self.assertFalse(stats["fallback_active"])  # Was requested explicitly
            self.assertEqual("SQLite (dev)", store.backend_name)
            await store.stop()

        asyncio.run(run())

    def test_postgres_store_strict_failure_when_fallback_disabled(self):
        async def run():
            store = PostgresStore(
                dsn="postgresql://invalid_user:invalid_pass@127.0.0.1:59999/invalid_db",
                required=True,
                fallback_to_sqlite=False,
            )
            with self.assertRaises(RuntimeError):
                await store.start(chart_limit=10, trade_limit=10, event_limit=10)

        asyncio.run(run())

    def test_state_manager_integration_with_sqlite(self):
        async def run():
            store = PostgresStore(
                dsn="postgresql://invalid_user:invalid_pass@127.0.0.1:59999/invalid_db",
                fallback_to_sqlite=True,
                sqlite_path=self.db_path,
            )
            manager = StateManager(persistence=store)
            await manager.initialize_persistence()

            readiness = manager.get_readiness()
            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["database_connected"])
            self.assertEqual("sqlite", readiness["backend"])
            manager.update_binance([[100.0, 1.0]], [[101.0, 1.0]], 100.0, 101.0)
            await manager.shutdown()
            await store.stop()

            self.assertTrue(manager.get_health()["shutting_down"])
            self.assertFalse(manager.get_readiness()["ready"])

        asyncio.run(run())

    def test_database_size_calculation_and_state_manager(self):
        async def run():
            size_db_path = os.path.join(self.temp_dir.name, "test_size.db")
            store = SqliteStore(db_path=size_db_path)
            await store.start(chart_limit=10, trade_limit=10, event_limit=10)

            # Insert sample data
            for i in range(10):
                store.record_chart_sample({"time": f"2026-09-06T00:00:0{i}Z", "binance": 50000.0 + i})

            # Check database size methods directly on store
            size_bytes = store.get_database_size_bytes()
            self.assertGreater(size_bytes, 0)

            formatted = store.get_database_size_formatted()
            self.assertEqual(False, "backend" in formatted) # store returns size_bytes, size_mb, size_gb, formatted
            self.assertGreater(formatted["size_bytes"], 0)
            self.assertGreater(formatted["size_mb"], 0)
            self.assertTrue(formatted["formatted"].endswith("MB") or formatted["formatted"].endswith("KB"))
            await store.stop()

            # Check via PostgresStore wrapper
            pg_store = PostgresStore(
                dsn="postgresql://invalid_user:invalid_pass@127.0.0.1:59999/invalid_db",
                fallback_to_sqlite=True,
                sqlite_path=size_db_path,
            )
            await pg_store.start(chart_limit=10, trade_limit=10, event_limit=10)
            pg_formatted = await pg_store.get_database_size_formatted()
            self.assertEqual("sqlite", pg_formatted["backend"])
            self.assertGreater(pg_formatted["size_bytes"], 0)

            # Check via StateManager
            manager = StateManager(persistence=pg_store)
            await manager.initialize_persistence()

            state_size = await manager.get_database_size()
            self.assertEqual("sqlite", state_size["backend"])
            self.assertGreater(state_size["size_bytes"], 0)
            self.assertIn("checked_at", state_size)
            self.assertIn("MB", state_size["formatted"])

            await manager.shutdown()
            await pg_store.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

