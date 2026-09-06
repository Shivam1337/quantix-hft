"""
Unit tests for WalletManager, SettingsManager, database persistence, and dual-mode execution routing.
"""
import os
import json
import shutil
import sqlite3
import tempfile
import unittest
from eth_account import Account
from app.core.wallet_manager import WalletManager
from app.core.settings_manager import SettingsManager
from app.core.sniper_engine import SniperEngine


class TestWalletAndSettings(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_db_path = os.path.join(self.temp_dir, "test_wallet.db")
        self.settings_db_path = os.path.join(self.temp_dir, "test_settings.db")

        self.wallet_mgr = WalletManager(db_path=self.wallet_db_path)
        self.settings_mgr = SettingsManager(db_path=self.settings_db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wallet_auto_generation(self):
        """Verify wallet is auto-created in SQLite DB with valid Ethereum L1 and Lighter zk-keys."""
        self.assertTrue(os.path.exists(self.wallet_db_path))
        self.assertTrue(self.wallet_mgr.address.startswith("0x"))
        self.assertEqual(len(self.wallet_mgr.address), 42)
        self.assertTrue(self.wallet_mgr.private_key.startswith("0x"))
        self.assertTrue(self.wallet_mgr.public_key.startswith("0x"))

        # Check Lighter zk-keys
        self.assertTrue(self.wallet_mgr.lighter_public_key.startswith("0x"))
        self.assertTrue(self.wallet_mgr.lighter_private_key.startswith("0x"))

        # Verify eth_account can reconstruct from private key
        reconstructed = Account.from_key(self.wallet_mgr.private_key)
        self.assertEqual(reconstructed.address, self.wallet_mgr.address)

        # Verify persisted directly in SQLite database table
        with sqlite3.connect(self.wallet_db_path) as conn:
            cursor = conn.execute("SELECT payload FROM wallet_credentials WHERE key = 'active'")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            data = json.loads(row[0])
            self.assertEqual(data["address"], self.wallet_mgr.address)

    def test_wallet_masking_and_reveal(self):
        """Verify private keys are masked in summary but complete in unmasked reveal."""
        summary = self.wallet_mgr.get_summary(mask_keys=True)
        self.assertIn("••••", summary["private_key"])
        self.assertIn("••••", summary["lighter_private_key"])

        revealed = self.wallet_mgr.get_unmasked_credentials()
        self.assertEqual(revealed["private_key"], self.wallet_mgr.private_key)
        self.assertEqual(revealed["lighter_private_key"], self.wallet_mgr.lighter_private_key)
        self.assertNotIn("••••", revealed["private_key"])

    def test_wallet_import_private_key(self):
        """Verify importing a known private key updates the address and generates fresh zk-keys in DB."""
        known_acc = Account.create()
        self.wallet_mgr.import_private_key(known_acc.key.hex())

        self.assertEqual(self.wallet_mgr.address, known_acc.address)
        self.assertEqual(self.wallet_mgr.private_key, f"0x{known_acc.key.hex().removeprefix('0x')}")
        self.assertTrue(self.wallet_mgr.lighter_public_key.startswith("0x"))

        # Check that DB was updated
        with sqlite3.connect(self.wallet_db_path) as conn:
            cursor = conn.execute("SELECT payload FROM wallet_credentials WHERE key = 'active'")
            data = json.loads(cursor.fetchone()[0])
            self.assertEqual(data["address"], known_acc.address)

    def test_settings_default_simulation_mode(self):
        """Verify default trading mode is SIMULATION."""
        self.assertEqual(self.settings_mgr.trading_mode, "SIMULATION")
        self.assertFalse(self.settings_mgr.is_real_mode)
        self.assertEqual(self.settings_mgr.network, "mainnet")

    def test_settings_real_mode_eligibility_safeguard(self):
        """Verify switching to REAL mode without valid account index fails gracefully."""
        success, msg = self.settings_mgr.set_trading_mode("REAL")
        self.assertFalse(success)
        self.assertEqual(self.settings_mgr.trading_mode, "SIMULATION")
        self.assertIn("Account Index", msg)

    def test_settings_update_and_persistence(self):
        """Verify settings updates persist to SQLite DB and reload."""
        self.settings_mgr.update_settings({
            "leverage": 30.0,
            "trade_margin_fraction": 0.40,
            "min_lag_trigger": 7.5,
        })
        self.assertEqual(self.settings_mgr.leverage, 30.0)
        self.assertEqual(self.settings_mgr.trade_margin_fraction, 0.40)
        self.assertEqual(self.settings_mgr.min_lag_trigger, 7.5)

        # Verify in SQLite table
        with sqlite3.connect(self.settings_db_path) as conn:
            cursor = conn.execute("SELECT payload FROM system_settings WHERE key = 'current'")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            data = json.loads(row[0])
            self.assertEqual(data["leverage"], 30.0)
            self.assertEqual(data["trade_margin_fraction"], 0.40)
            self.assertEqual(data["min_lag_trigger"], 7.5)

        # Reload from DB
        reloaded = SettingsManager(db_path=self.settings_db_path)
        self.assertEqual(reloaded.leverage, 30.0)
        self.assertEqual(reloaded.trade_margin_fraction, 0.40)
        self.assertEqual(reloaded.min_lag_trigger, 7.5)

    def test_no_json_files_written_to_disk(self):
        """Verify zero .json configuration or wallet files exist on disk."""
        files = os.listdir(self.temp_dir)
        json_files = [f for f in files if f.endswith(".json")]
        self.assertEqual(json_files, [], f"Expected zero JSON files on disk, found: {json_files}")

    def test_legacy_json_migration_and_auto_cleanup(self):
        """Verify legacy wallet.json and settings.json are migrated into DB and deleted from disk."""
        sub_dir = os.path.join(self.temp_dir, "migration_test")
        os.makedirs(sub_dir, exist_ok=True)

        legacy_wallet = os.path.join(sub_dir, "wallet.json")
        legacy_settings = os.path.join(sub_dir, "settings.json")
        db_file = os.path.join(sub_dir, "migrated.db")

        test_acc = Account.create()
        with open(legacy_wallet, "w", encoding="utf-8") as f:
            json.dump({
                "address": test_acc.address,
                "private_key": test_acc.key.hex(),
                "public_key": "0x1234",
                "lighter_public_key": "0x5678",
                "lighter_private_key": "0x9abc",
            }, f)

        with open(legacy_settings, "w", encoding="utf-8") as f:
            json.dump({
                "trading_mode": "SIMULATION",
                "leverage": 42.0,
            }, f)

        self.assertTrue(os.path.exists(legacy_wallet))
        self.assertTrue(os.path.exists(legacy_settings))

        # Initializing managers triggers auto-migration to DB
        wm = WalletManager(db_path=db_file)
        sm = SettingsManager(db_path=db_file)

        # Verify data migrated to DB
        self.assertEqual(wm.address, test_acc.address)
        self.assertEqual(sm.leverage, 42.0)

        # Verify legacy JSON files were automatically cleaned up from disk
        self.assertFalse(os.path.exists(legacy_wallet), "Legacy wallet.json was not deleted after migration!")
        self.assertFalse(os.path.exists(legacy_settings), "Legacy settings.json was not deleted after migration!")

    def test_sniper_engine_dynamic_sizing_integration(self):
        """Verify SniperEngine integrates settings_manager dynamic leverage and margin."""
        engine = SniperEngine()
        calc = engine.calculate_trade_size(80000.0)
        self.assertGreater(calc["size_btc"], 0.0)
        self.assertEqual(calc["leverage"], 50.0)

        perf = engine.get_performance()
        self.assertIn("trading_mode", perf)
        self.assertIn("is_real_mode", perf)
        self.assertIn("paper_only", perf)

    def test_simulation_starting_balance_persistence(self):
        """Verify simulation_starting_balance updates and reloads from SQLite DB."""
        self.assertEqual(self.settings_mgr.simulation_starting_balance, 100.0)
        self.settings_mgr.update_settings({"simulation_starting_balance": 500.0})
        self.assertEqual(self.settings_mgr.simulation_starting_balance, 500.0)

        # Reload from DB
        reloaded = SettingsManager(db_path=self.settings_db_path)
        self.assertEqual(reloaded.simulation_starting_balance, 500.0)

        # Restore default
        self.settings_mgr.update_settings({"simulation_starting_balance": 100.0})

    def test_sniper_engine_sizing_scales_with_starting_balance(self):
        """Verify SniperEngine dynamically scales trade sizing when simulation starting balance changes."""
        engine = SniperEngine()
        engine.closed_trades.clear()
        
        # At $100 starting balance: 50% = $50 margin, 50x = $2500 notional
        engine.base_balance_usd = 100.0
        calc_100 = engine.calculate_trade_size(80000.0)
        self.assertEqual(calc_100["margin_allocated_usd"], 50.0)
        self.assertEqual(calc_100["notional_usd"], 2500.0)

        # At $500 starting balance: 50% = $250 margin, 50x = $12500 notional
        engine.base_balance_usd = 500.0
        calc_500 = engine.calculate_trade_size(80000.0)
        self.assertEqual(calc_500["margin_allocated_usd"], 250.0)
        self.assertEqual(calc_500["notional_usd"], 12500.0)

    def test_reset_simulation_lifecycle(self):
        """Verify SniperEngine.reset_simulation() wipes all paper trades and resets stance to initial state."""
        engine = SniperEngine()
        engine._seed_baseline_trades()
        self.assertGreater(len(engine.closed_trades), 0)
        self.assertGreater(engine.trade_counter, 0)

        engine.reset_simulation()
        self.assertEqual(len(engine.closed_trades), 0)
        self.assertEqual(engine.trade_counter, 0)
        self.assertIsNone(engine.active_trade)
        self.assertEqual(engine.current_decision["stance"], "MONITORING")

        perf = engine.get_performance()
        self.assertEqual(perf["total_trades"], 0)
        self.assertEqual(perf["wins"], 0)
        self.assertEqual(perf["losses"], 0)
        self.assertEqual(perf["net_pnl"], 0.0)


if __name__ == "__main__":
    unittest.main()
