"""
Wallet Manager.
Generates, persists, and manages Ethereum/Arbitrum L1 wallets and Lighter.xyz zk-keys.
Queries live balances from Arbitrum RPC and Lighter.xyz endpoints.
"""
import os
import json
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import aiohttp
from eth_account import Account
import lighter
from app.config import SQLITE_DB_PATH
from app.core.lighter_account import empty_lighter_account_balances, parse_lighter_account_response

logger = logging.getLogger("wallet_manager")

ARBITRUM_RPC_URL = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
LIGHTER_BASE_URL = os.getenv("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")


class WalletManager:
    """Manages the server's local Ethereum L1 wallet and Lighter zk-credentials in the database."""

    def __init__(self, db_path: Optional[str] = None, wallet_path: Optional[str] = None) -> None:
        if db_path is not None:
            self.db_path = db_path
        elif wallet_path is not None:
            self.db_path = wallet_path[:-5] + ".db" if wallet_path.endswith(".json") else wallet_path
        else:
            self.db_path = SQLITE_DB_PATH

        self.wallet_path = self.db_path
        self._wallet_data: Dict[str, Any] = {}
        self._balances: Dict[str, Any] = {
            "arbitrum_eth": 0.0,
            **empty_lighter_account_balances(),
            "last_checked": None,
        }
        self._lock = asyncio.Lock()
        self._ensure_wallet_exists()

    def _ensure_wallet_exists(self) -> None:
        """Loads existing wallet credentials from DB, migrates legacy wallet.json if present, or creates a new wallet in the DB."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        # 1. Load from DB
        data = self._load_from_db()
        if data and data.get("address") and data.get("private_key"):
            self._wallet_data = data
            logger.info("Loaded server wallet from DB (%s): %s", self.db_path, data.get("address"))
            self._clean_legacy_json()
            return

        # 2. Check for legacy wallet.json for automatic migration to DB
        legacy_path = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "wallet.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                if legacy_data.get("address") and legacy_data.get("private_key"):
                    self._wallet_data = legacy_data
                    self._save_to_db()
                    logger.info("Migrated legacy wallet.json to DB (%s): %s", self.db_path, legacy_data.get("address"))
                    self._clean_legacy_json(legacy_path)
                    return
            except Exception as e:
                logger.warning("Failed to migrate legacy wallet.json: %s", e)

        # 3. Generate fresh wallet in DB
        self._create_new_wallet_data()

    def _clean_legacy_json(self, path: Optional[str] = None) -> None:
        """Removes legacy plaintext JSON wallet file from disk if present."""
        try:
            target = path or os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "wallet.json")
            if os.path.exists(target):
                os.remove(target)
                logger.info("Removed legacy plaintext JSON wallet file from disk: %s", target)
        except Exception as e:
            logger.warning("Could not remove legacy wallet JSON: %s", e)

    def _create_new_wallet_data(self) -> Dict[str, Any]:
        """Creates a fresh Ethereum wallet and Lighter zk-key pair and saves to the database."""
        eth_acc = Account.create()
        address = eth_acc.address
        private_key = eth_acc.key.hex()
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        public_key = eth_acc._key_obj.public_key.to_hex()

        # Generate Lighter zk-key pair using lighter-sdk Windows/native DLL
        lighter_pub, lighter_priv = "", ""
        try:
            k = lighter.create_api_key()
            if isinstance(k, tuple) and len(k) >= 2:
                lighter_pub = str(k[0])
                lighter_priv = str(k[1])
        except Exception as exc:
            logger.warning("Could not auto-generate Lighter API key pair: %s", exc)

        data = {
            "address": address,
            "private_key": private_key,
            "public_key": public_key,
            "lighter_public_key": lighter_pub,
            "lighter_private_key": lighter_priv,
            "lighter_account_index": None,
            "lighter_api_key_index": 4,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        self._wallet_data = data
        self._save_to_db()
        logger.info("Generated new server wallet in DB: %s", address)
        return data

    def _save_to_db(self) -> None:
        """Persists wallet data directly to the database (zero JSON files on disk)."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        try:
            encoded = json.dumps(self._wallet_data, default=str)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS wallet_credentials (
                        key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO wallet_credentials (key, payload, updated_at)
                    VALUES ('active', ?, DATETIME('now'))
                    ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                    """,
                    (encoded,),
                )
        except Exception as e:
            logger.error("Failed to save wallet credentials to DB (%s): %s", self.db_path, e)

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
                    state_manager.persistence.save_wallet_credentials(self._wallet_data, key="active")
                )
        except Exception:
            pass

    def _load_from_db(self) -> Optional[Dict[str, Any]]:
        """Loads wallet credentials from the database."""
        if self.db_path != ":memory:" and not os.path.exists(self.db_path):
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS wallet_credentials (
                        key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                    );
                    """
                )
                cursor.execute("SELECT payload FROM wallet_credentials WHERE key = 'active'")
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
                return None
        except Exception as e:
            logger.warning("Could not load wallet credentials from DB (%s): %s", self.db_path, e)
            return None


    @property
    def address(self) -> str:
        return self._wallet_data.get("address", "")

    @property
    def public_key(self) -> str:
        return self._wallet_data.get("public_key", "")

    @property
    def private_key(self) -> str:
        return self._wallet_data.get("private_key", "")

    @property
    def lighter_public_key(self) -> str:
        return self._wallet_data.get("lighter_public_key", "")

    @property
    def lighter_private_key(self) -> str:
        return self._wallet_data.get("lighter_private_key", "")

    @property
    def lighter_account_index(self) -> Optional[int]:
        return self._wallet_data.get("lighter_account_index")

    @property
    def lighter_api_key_index(self) -> int:
        return int(self._wallet_data.get("lighter_api_key_index", 4))

    def set_lighter_account_index(self, index: Optional[int]) -> None:
        self._wallet_data["lighter_account_index"] = index
        self._wallet_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_to_db()

    def set_lighter_api_keys(self, pub_key: str, priv_key: str, key_index: int = 4) -> None:
        self._wallet_data["lighter_public_key"] = pub_key
        self._wallet_data["lighter_private_key"] = priv_key
        self._wallet_data["lighter_api_key_index"] = key_index
        self._wallet_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_to_db()

    @staticmethod
    def _mask(value: str) -> str:
        if not value or len(value) <= 10:
            return "••••••••••••"
        return f"{value[:6]}••••••••{value[-4:]}"

    def get_summary(self, mask_keys: bool = True) -> Dict[str, Any]:
        """Returns wallet summary for the UI and API, optionally masking private keys."""
        priv = self.private_key
        lighter_priv = self.lighter_private_key
        return {
            "address": self.address,
            "public_key": self.public_key,
            "private_key": self._mask(priv) if mask_keys else priv,
            "lighter_public_key": self.lighter_public_key,
            "lighter_private_key": self._mask(lighter_priv) if mask_keys else lighter_priv,
            "lighter_account_index": self.lighter_account_index or self._balances.get("lighter_account_index"),
            "lighter_api_key_index": self.lighter_api_key_index,
            "balances": dict(self._balances),
            "created_at": self._wallet_data.get("created_at"),
            "updated_at": self._wallet_data.get("updated_at"),
            "is_funded": (self._balances.get("lighter_collateral_usd", 0.0) > 0.0 or self._balances.get("arbitrum_eth", 0.0) > 0.0),
        }

    def get_unmasked_credentials(self) -> Dict[str, str]:
        """Returns raw unmasked credentials for explicit user export."""
        return {
            "address": self.address,
            "public_key": self.public_key,
            "private_key": self.private_key,
            "lighter_public_key": self.lighter_public_key,
            "lighter_private_key": self.lighter_private_key,
            "lighter_account_index": str(self.lighter_account_index or ""),
            "lighter_api_key_index": str(self.lighter_api_key_index),
        }

    def generate_new_wallet(self) -> Dict[str, Any]:
        """Replaces the current wallet with a newly generated one."""
        self._balances = {
            "arbitrum_eth": 0.0,
            **empty_lighter_account_balances(),
            "last_checked": None,
        }
        return self._create_new_wallet_data()

    def import_private_key(self, private_key_hex: str) -> Dict[str, Any]:
        """Imports an existing private key and updates the wallet."""
        clean_key = private_key_hex.strip()
        if not clean_key.startswith("0x"):
            clean_key = f"0x{clean_key}"

        eth_acc = Account.from_key(clean_key)
        address = eth_acc.address
        public_key = eth_acc._key_obj.public_key.to_hex()

        # Generate Lighter zk-key pair for this account
        lighter_pub, lighter_priv = "", ""
        try:
            k = lighter.create_api_key()
            if isinstance(k, tuple) and len(k) >= 2:
                lighter_pub = str(k[0])
                lighter_priv = str(k[1])
        except Exception as exc:
            logger.warning("Could not auto-generate Lighter API key pair on import: %s", exc)

        data = {
            "address": address,
            "private_key": clean_key,
            "public_key": public_key,
            "lighter_public_key": lighter_pub,
            "lighter_private_key": lighter_priv,
            "lighter_account_index": None,
            "lighter_api_key_index": 4,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._wallet_data = data
        self._save_to_db()
        self._balances["lighter_account_index"] = None
        self._balances["lighter_account_status"] = "UNREGISTERED"
        logger.info("Imported wallet: %s", address)
        return data

    async def refresh_balances(self) -> Dict[str, Any]:
        """Asynchronously checks Arbitrum L1 ETH balance and Lighter zkRollup collateral."""
        async with self._lock:
            addr = self.address
            if not addr:
                return self._balances

            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

            # 1. Arbitrum ETH balance via JSON-RPC
            arb_eth = 0.0
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0)) as session:
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "eth_getBalance",
                        "params": [addr, "latest"],
                        "id": 1,
                    }
                    async with session.post(ARBITRUM_RPC_URL, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if "result" in data and data["result"]:
                                wei = int(data["result"], 16)
                                arb_eth = round(wei / 1e18, 6)
            except Exception as e:
                logger.debug("Arbitrum balance check skipped/failed: %s", e)

            # 2. Lighter account equity/free margin via its public account API.
            account_index = self.lighter_account_index
            account_snapshot = empty_lighter_account_balances(
                status="UNKNOWN" if account_index else "UNREGISTERED",
            )

            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0)) as session:
                    # If account_index not set, query by L1 address
                    if not account_index:
                        acc_by_l1_url = f"{LIGHTER_BASE_URL}/api/v1/accountsByL1Address?l1_address={addr}"
                        async with session.get(acc_by_l1_url) as resp:
                            if resp.status == 200:
                                d = await resp.json()
                                sub_accounts = d.get("sub_accounts", [])
                                if sub_accounts:
                                    account_index = int(sub_accounts[0].get("index", 0))
                                    self.set_lighter_account_index(account_index)

                    if account_index:
                        acc_url = f"{LIGHTER_BASE_URL}/api/v1/account?by=index&value={account_index}"
                        async with session.get(acc_url) as resp:
                            if resp.status == 200:
                                account_snapshot = parse_lighter_account_response(await resp.json()) or account_snapshot
                                account_index = account_snapshot.get("lighter_account_index") or account_index
                                if account_index and self.lighter_account_index != account_index:
                                    self.set_lighter_account_index(account_index)
            except Exception as e:
                logger.debug("Lighter account check skipped/failed: %s", e)

            self._balances = {
                "arbitrum_eth": arb_eth,
                **account_snapshot,
                "lighter_account_index": account_index,
                "last_checked": now_str,
            }
            return self._balances


# Global Singleton Instance
wallet_manager = WalletManager()
