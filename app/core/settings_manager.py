"""
Settings Manager.
Persists and manages system configuration, runtime trading mode (SIMULATION vs REAL),
Lighter network, API keys, and risk/sizing parameters.
"""
import os
import json
import logging
import asyncio
import sqlite3
from typing import Dict, Any, Tuple, Optional
from app.core.wallet_manager import wallet_manager
from app.config import SQLITE_DB_PATH

logger = logging.getLogger("settings_manager")


class SettingsManager:
    """Manages persistent runtime settings and mode toggling in the database."""

    DEFAULT_SETTINGS = {
        "trading_mode": "SIMULATION",  # "SIMULATION" or "REAL"
        "trading_enabled": True,        # Global entry kill switch for both modes
        "network": "mainnet",          # "mainnet" or "testnet"
        "account_index": None,
        "api_key_index": 4,
        "api_private_key": "",
        "trade_margin_fraction": 0.50,
        "leverage": 50.0,
        "min_lag_trigger": 6.00,
        "max_hold_seconds": 12.0,
        "stop_loss_drawdown": 8.0,
        "simulation_starting_balance": 100.0,
    }

    def __init__(self, db_path: Optional[str] = None, settings_path: Optional[str] = None) -> None:
        if db_path is not None:
            self.db_path = db_path
        elif settings_path is not None:
            self.db_path = settings_path[:-5] + ".db" if settings_path.endswith(".json") else settings_path
        else:
            self.db_path = SQLITE_DB_PATH

        self.settings_path = self.db_path
        self._settings: Dict[str, Any] = dict(self.DEFAULT_SETTINGS)
        self._ensure_settings_exist()

    def _ensure_settings_exist(self) -> None:
        """Loads settings from DB, migrates legacy settings.json if present, or seeds defaults in the DB."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        # 1. Load from DB
        data = self._load_from_db()
        if data:
            self._settings.update(data)
            logger.info("Loaded settings from DB (%s, Mode: %s)", self.db_path, self._settings.get("trading_mode"))
            self._clean_legacy_json()
            return

        # 2. Check for legacy settings.json for automatic migration to DB
        legacy_path = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "settings.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                if legacy_data:
                    self._settings.update(legacy_data)
                    self._save_to_db()
                    logger.info("Migrated legacy settings.json to DB (%s, Mode: %s)", self.db_path, self._settings.get("trading_mode"))
                    self._clean_legacy_json(legacy_path)
                    return
            except Exception as e:
                logger.warning("Failed to migrate legacy settings.json: %s", e)

        # 3. Seed defaults in DB
        self._save_to_db()

    def _clean_legacy_json(self, path: Optional[str] = None) -> None:
        """Removes legacy plaintext JSON settings file from disk if present."""
        try:
            target = path or os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "settings.json")
            if os.path.exists(target):
                os.remove(target)
                logger.info("Removed legacy plaintext JSON settings file from disk: %s", target)
        except Exception as e:
            logger.warning("Could not remove legacy settings JSON: %s", e)

    def _save_to_db(self) -> None:
        """Persists settings directly to the database (zero JSON files on disk)."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        try:
            encoded = json.dumps(self._settings, default=str)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO system_settings (key, payload, updated_at)
                    VALUES ('current', ?, DATETIME('now'))
                    ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                    """,
                    (encoded,),
                )
        except Exception as e:
            logger.error("Failed to save system settings to DB (%s): %s", self.db_path, e)

        self._sync_to_postgres_if_available()

    def _sync_to_postgres_if_available(self) -> None:
        """Schedules async sync to PostgreSQL if connected."""
        try:
            from app.core.state_manager import state_manager
            if (
                state_manager
                and hasattr(state_manager, "persistence")
                and state_manager.persistence
                and getattr(state_manager.persistence, "_pool", None) is not None
            ):
                asyncio.create_task(
                    state_manager.persistence.save_system_settings(self._settings, key="current")
                )
        except Exception:
            pass

    def _load_from_db(self) -> Optional[Dict[str, Any]]:
        """Loads settings from the database."""
        if self.db_path != ":memory:" and not os.path.exists(self.db_path):
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                    );
                    """
                )
                cursor.execute("SELECT payload FROM system_settings WHERE key = 'current'")
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
                return None
        except Exception as e:
            logger.warning("Could not load system settings from DB (%s): %s", self.db_path, e)
            return None

    @property
    def trading_mode(self) -> str:
        return str(self._settings.get("trading_mode", "SIMULATION")).upper()

    @property
    def is_real_mode(self) -> bool:
        return self.trading_mode == "REAL"

    @property
    def trading_enabled(self) -> bool:
        """Whether new simulated or real entries are globally permitted."""
        value = self._settings.get("trading_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    @property
    def network(self) -> str:
        return str(self._settings.get("network", "mainnet")).lower()

    @property
    def account_index(self) -> int:
        idx = self._settings.get("account_index")
        if idx is not None:
            try:
                return int(idx)
            except (ValueError, TypeError):
                pass
        w_idx = wallet_manager.lighter_account_index
        return int(w_idx) if w_idx is not None else 0

    @property
    def api_key_index(self) -> int:
        return int(self._settings.get("api_key_index", wallet_manager.lighter_api_key_index or 4))

    @property
    def api_private_key(self) -> str:
        k = self._settings.get("api_private_key", "")
        if not k:
            k = wallet_manager.lighter_private_key
        return str(k)

    @property
    def trade_margin_fraction(self) -> float:
        return float(self._settings.get("trade_margin_fraction", 0.50))

    @property
    def leverage(self) -> float:
        return float(self._settings.get("leverage", 50.0))

    @property
    def min_lag_trigger(self) -> float:
        return float(self._settings.get("min_lag_trigger", 6.00))

    @property
    def simulation_starting_balance(self) -> float:
        try:
            return float(self._settings.get("simulation_starting_balance", 100.0))
        except (TypeError, ValueError):
            return 100.0

    def check_real_mode_eligibility(self) -> Tuple[bool, str]:
        """Validates whether required credentials are set to trade live on Lighter."""
        acc_idx = self.account_index
        priv_key = self.api_private_key

        if acc_idx <= 0:
            return False, "Lighter Account Index is not set. Fund wallet and register API key on Lighter."
        if not priv_key or len(priv_key) < 10:
            return False, "Lighter API Private Key is missing or invalid."
        return True, "Ready for live trading."

    def set_trading_mode(self, mode: str) -> Tuple[bool, str]:
        clean = mode.strip().upper()
        if clean not in ("SIMULATION", "REAL"):
            return False, f"Invalid trading mode '{mode}'. Must be 'SIMULATION' or 'REAL'."

        if clean == "REAL":
            eligible, reason = self.check_real_mode_eligibility()
            if not eligible:
                logger.warning("Cannot switch to REAL mode: %s. Reverting to SIMULATION.", reason)
                self._settings["trading_mode"] = "SIMULATION"
                self._save_to_db()
                return False, f"Cannot switch to REAL mode: {reason}"

        self._settings["trading_mode"] = clean
        self._save_to_db()
        logger.info("Switched trading mode to: %s", clean)
        return True, f"Trading mode successfully set to {clean}."

    def set_trading_enabled(self, enabled: bool) -> Tuple[bool, str]:
        """Persist the global entry kill switch without changing the selected mode."""
        if not isinstance(enabled, bool):
            return False, "Trading activity must be enabled or disabled with a boolean value."
        self._settings["trading_enabled"] = enabled
        self._save_to_db()
        status = "enabled" if enabled else "paused"
        logger.warning("Global trading activity %s.", status)
        return True, f"Global trading activity is {status}."

    def update_settings(self, updates: Dict[str, Any]) -> Tuple[bool, str]:
        allowed_keys = {
            "trading_mode",
            "trading_enabled",
            "network",
            "account_index",
            "api_key_index",
            "api_private_key",
            "trade_margin_fraction",
            "leverage",
            "min_lag_trigger",
            "max_hold_seconds",
            "stop_loss_drawdown",
            "simulation_starting_balance",
        }
        for k, v in updates.items():
            if k in allowed_keys:
                if k == "trading_mode":
                    self.set_trading_mode(str(v))
                elif k == "trading_enabled":
                    success, message = self.set_trading_enabled(v)
                    if not success:
                        return False, message
                else:
                    self._settings[k] = v

        self._save_to_db()
        return True, "Settings updated successfully."

    def get_summary(self, mask_keys: bool = True) -> Dict[str, Any]:
        eligible, reason = self.check_real_mode_eligibility()
        priv = self.api_private_key
        masked_priv = f"{priv[:6]}••••••••{priv[-4:]}" if priv and len(priv) > 10 else "••••••••••••"

        return {
            "trading_mode": self.trading_mode,
            "trading_enabled": self.trading_enabled,
            "network": self.network,
            "account_index": self.account_index,
            "api_key_index": self.api_key_index,
            "api_private_key": masked_priv if mask_keys else priv,
            "trade_margin_fraction": self.trade_margin_fraction,
            "leverage": self.leverage,
            "min_lag_trigger": self.min_lag_trigger,
            "max_hold_seconds": float(self._settings.get("max_hold_seconds", 12.0)),
            "stop_loss_drawdown": float(self._settings.get("stop_loss_drawdown", 8.0)),
            "simulation_starting_balance": self.simulation_starting_balance,
            "is_real_eligible": eligible,
            "eligibility_message": reason,
        }


# Global Singleton Instance
settings_manager = SettingsManager()
