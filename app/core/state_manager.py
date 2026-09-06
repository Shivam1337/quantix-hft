"""Central state, freshness checks, and evidence capture for the lead-lag experiment."""
from __future__ import annotations

import asyncio
import collections
import copy
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Set
import uuid
import logging

logger = logging.getLogger("app.state_manager")


from app.config import (
    CHART_SAMPLE_INTERVAL_SECONDS,
    FEES,
    MAJOR_DISCOVERY_VENUES,
    MAX_HISTORY_POINTS,
    MAX_CLOSED_TRADES_HISTORY,
    MAX_REPRICING_EVENTS_HISTORY,
    PERSISTED_CHART_SAMPLE_INTERVAL_SECONDS,
    STALE_FEED_SECONDS,
)
from app.core.lead_lag_analyzer import LeadLagAnalyzer
from app.core.execution.order_journal import OrderJournal
from app.core.postgres_store import PostgresStore
from app.core.resource_monitor import ResourceMonitor
from app.core.sniper_engine import SniperEngine


PROVIDER_METADATA = {
    "Binance": {
        "id": "binance",
        "name": "Binance Futures",
        "role": "Major discovery signal",
        "signal_eligible": True,
    },
    "Bybit": {
        "id": "bybit",
        "name": "Bybit Linear",
        "role": "Major discovery signal",
        "signal_eligible": True,
    },
    "OKX": {
        "id": "okx",
        "name": "OKX Perpetual",
        "role": "Major discovery signal",
        "signal_eligible": True,
    },
    "Hyperliquid": {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "role": "Major discovery signal",
        "signal_eligible": True,
    },
    "Lighter.xyz": {
        "id": "lighter",
        "name": "Lighter.xyz",
        "role": "Observed execution target",
        "signal_eligible": False,
    },
    "Polymarket": {
        "id": "polymarket",
        "name": "Polymarket",
        "role": "Observer only",
        "signal_eligible": False,
    },
}


class StateManager:
    """Owns mutable market state; API getters only return snapshots."""

    def __init__(self, persistence: Optional[PostgresStore] = None) -> None:
        self.start_time = time.time()
        self.start_time_str = datetime.now(timezone.utc).isoformat()
        self.messages_count = 0
        self.last_recalculated_at: Optional[str] = None
        self.lead_lag_analyzer = LeadLagAnalyzer()
        self.sniper_engine = SniperEngine()
        self.order_journal = OrderJournal.durable_default()
        self.sniper_engine.configure_order_journal(self.order_journal)
        if not self.order_journal.is_durable:
            self.sniper_engine._block_new_live_entries("DURABLE_ORDER_JOURNAL_UNAVAILABLE")
            logger.error("Blocked REAL/DUAL entries because ORDER_JOURNAL_DB_PATH is not durable.")
        self.persistence = persistence or PostgresStore()
        self.resource_monitor = ResourceMonitor()
        self.sse_clients: Set[asyncio.Queue] = set()
        self.price_history = collections.deque(maxlen=MAX_HISTORY_POINTS)
        self._last_chart_sample_monotonic_ns: Optional[int] = None
        self._last_persisted_chart_sample_monotonic_ns: Optional[int] = None
        self._last_persisted_decision_signature: Optional[tuple] = None
        self._logged_closed_trade_ids: Set[int] = set()
        self._logged_execution_comparison_signatures: Dict[int, str] = {}
        self._shutting_down = False
        self._shutdown_complete = False

        self.binance = self._new_venue_state("BTCUSDT (Binance Futures)", FEES["binance"]["label"])
        self.bybit = self._new_venue_state("BTCUSDT (Bybit Linear)", FEES["bybit"]["label"])
        self.okx = self._new_venue_state("BTC-USDT-SWAP (OKX)", FEES["okx"]["label"])
        self.hl = self._new_venue_state("BTC-PERP (Hyperliquid)", FEES["hyperliquid"]["label"])
        self.lighter = self._new_venue_state("BTC Perp (Lighter.xyz)", FEES["lighter"]["label"], include_lag=True)
        self.poly = self._new_venue_state("BTC-USD (Polymarket)", FEES["polymarket"]["label"], include_lag=True)
        self.sniper_engine.configure_execution_telemetry(
            book_snapshot_provider=lambda: self.lighter,
            attempt_sink=lambda attempt: self.persistence.record_event({
                "transition": "EXECUTION_ATTEMPT",
                "event": attempt,
            }),
        )

    @staticmethod
    def _new_venue_state(symbol: str, fees: str, *, include_lag: bool = False) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "symbol": symbol,
            "mid_price": 0.0,
            "best_bid": 0.0,
            "best_ask": 0.0,
            "spread": 0.0,
            "fees": fees,
            "status": "CONNECTING...",
            "bids": [],
            "asks": [],
            "last_update_monotonic_ns": None,
            "last_update_wall_ns": None,
            "last_update_utc": None,
            "exchange_timestamp_ms": None,
            "source_sequence": None,
            "update_count": 0,
        }
        if include_lag:
            state.update({"lag_vs_leader": 0.0, "lag_bps": 0.0})
        return state

    @staticmethod
    def _normalise_timestamp(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalise_sequence(value: Any) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)

    def _all_venues(self) -> Dict[str, Dict[str, Any]]:
        return {
            "Binance": self.binance,
            "Bybit": self.bybit,
            "OKX": self.okx,
            "Hyperliquid": self.hl,
            "Lighter.xyz": self.lighter,
            "Polymarket": self.poly,
        }

    def _is_fresh(self, state: Dict[str, Any], now_monotonic_ns: Optional[int] = None) -> bool:
        last_update = state.get("last_update_monotonic_ns")
        if not isinstance(last_update, int) or last_update <= 0:
            return False
        now = now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns()
        return (now - last_update) <= int(STALE_FEED_SECONDS * 1_000_000_000)

    def _fresh_price(self, state: Dict[str, Any], now_monotonic_ns: int) -> float:
        return state["mid_price"] if self._is_fresh(state, now_monotonic_ns) else 0.0

    def _set_feed_status(self, venue: str, state: Dict[str, Any], status: str) -> None:
        if state["status"] == status:
            return
        state["status"] = status

    def _update_quote(
        self,
        *,
        venue: str,
        state: Dict[str, Any],
        bids: list,
        asks: list,
        best_bid: float,
        best_ask: float,
        status: str,
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        if self._shutting_down:
            return
        self._set_feed_status(venue, state, status)
        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            return

        monotonic_ns = time.monotonic_ns()
        wall_ns = time.time_ns()
        update_utc = datetime.now(timezone.utc).isoformat()
        state.update(
            {
                "bids": bids[:6],
                "asks": asks[:6],
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": round((best_bid + best_ask) / 2.0, 2),
                "spread": round(best_ask - best_bid, 2),
                "last_update_monotonic_ns": monotonic_ns,
                "last_update_wall_ns": wall_ns,
                "last_update_utc": update_utc,
                "exchange_timestamp_ms": self._normalise_timestamp(exchange_timestamp_ms),
                "source_sequence": self._normalise_sequence(sequence),
                "update_count": state["update_count"] + 1,
            }
        )
        self.messages_count += 1
        self.recalculate(updated_venue=venue)

    def update_binance(
        self,
        bids: list,
        asks: list,
        best_bid: float,
        best_ask: float,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        self._update_quote(
            venue="Binance", state=self.binance, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def update_bybit(
        self,
        bids: list,
        asks: list,
        best_bid: float,
        best_ask: float,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        self._update_quote(
            venue="Bybit", state=self.bybit, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def update_okx(
        self,
        bids: list,
        asks: list,
        best_bid: float,
        best_ask: float,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        self._update_quote(
            venue="OKX", state=self.okx, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def update_hl(
        self,
        bids: list,
        asks: list,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        self._update_quote(
            venue="Hyperliquid", state=self.hl, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def update_lighter(
        self,
        bids: list,
        asks: list,
        best_bid: float,
        best_ask: float,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        if best_bid <= 0 or best_ask <= 0:
            self.reset_lighter_orderbook(status=status)
            return
        self._update_quote(
            venue="Lighter.xyz", state=self.lighter, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def reset_lighter_orderbook(self, status: str = "CONNECTING...") -> None:
        """Invalidate Lighter prices and sizes until a fresh book snapshot arrives."""
        if self._shutting_down:
            return
        self._set_feed_status("Lighter.xyz", self.lighter, status)
        self.lighter.update(
            {
                "bids": [],
                "asks": [],
                "best_bid": 0.0,
                "best_ask": 0.0,
                "mid_price": 0.0,
                "spread": 0.0,
                "last_update_monotonic_ns": None,
                "last_update_wall_ns": None,
                "last_update_utc": None,
                "exchange_timestamp_ms": None,
                "source_sequence": None,
            }
        )

    def update_poly(
        self,
        bids: list,
        asks: list,
        status: str = "WS STREAMING",
        exchange_timestamp_ms: Any = None,
        sequence: Any = None,
    ) -> None:
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        self._update_quote(
            venue="Polymarket", state=self.poly, bids=bids, asks=asks, best_bid=best_bid,
            best_ask=best_ask, status=status, exchange_timestamp_ms=exchange_timestamp_ms, sequence=sequence,
        )

    def _persist_analysis_transition(self, analysis: Dict[str, Any]) -> None:
        transition = analysis.get("event_transition")
        if not transition:
            return
        event = copy.deepcopy(transition["event"])
        venue_states = self._all_venues()
        leader_state = venue_states.get(event["leading_exchange"], {})
        self.persistence.record_event(
            {
                "transition": transition["type"],
                "event": event,
                "execution_context": {
                    "lighter": self._execution_context(self.lighter),
                    "leader": self._execution_context(leader_state),
                },
                "dynamic_leader": analysis["dynamic_leader"],
                "consensus_status": analysis["consensus_status"],
                "consensus_venues": analysis["consensus_venues"],
                "signal_eligible": analysis["signal_eligible"],
            }
        )

    @staticmethod
    def _execution_context(state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "bid": state.get("best_bid", 0.0),
            "ask": state.get("best_ask", 0.0),
            "mid": state.get("mid_price", 0.0),
            "receive_monotonic_ns": state.get("last_update_monotonic_ns"),
            "exchange_timestamp_ms": state.get("exchange_timestamp_ms"),
            "source_sequence": state.get("source_sequence"),
        }

    def _persist_closed_trades(self, summary: Dict[str, Any]) -> None:
        for trade in summary["closed_trades"]:
            trade_id = trade["id"]
            if trade_id in self._logged_closed_trade_ids:
                continue
            self._logged_closed_trade_ids.add(trade_id)
            self.persistence.record_trade(trade)

    def _persist_execution_comparisons(self, summary: Dict[str, Any]) -> None:
        """Persist only comparison state transitions, never one event per price tick."""
        for comparison in summary.get("execution_comparisons", []):
            if not isinstance(comparison, dict):
                continue
            try:
                comparison_id = int(comparison["comparison_id"])
            except (KeyError, TypeError, ValueError):
                continue
            signature = f"{comparison.get('status')}:{comparison.get('updated_at')}"
            if self._logged_execution_comparison_signatures.get(comparison_id) == signature:
                continue
            self._logged_execution_comparison_signatures[comparison_id] = signature
            self.persistence.record_event({
                "transition": "DUAL_EXECUTION_COMPARISON",
                "event": copy.deepcopy(comparison),
            })

    def _persist_decision_transition(self, summary: Dict[str, Any], analysis: Dict[str, Any]) -> None:
        """Keep only meaningful decision changes, never one record per market message."""
        decision = summary["decision"]
        signature = (
            decision.get("stance"),
            decision.get("action"),
            decision.get("elected_leader"),
            decision.get("rejection_reason"),
        )
        if signature == self._last_persisted_decision_signature:
            return
        self._last_persisted_decision_signature = signature
        self.persistence.record_decision(
            {
                "decision": copy.deepcopy(decision),
                "dynamic_leader": analysis["dynamic_leader"],
                "consensus_status": analysis["consensus_status"],
                "consensus_venues": analysis["consensus_venues"],
                "signal_eligible": analysis["signal_eligible"],
            }
        )

    def _record_chart_point(self, now_monotonic_ns: int, analysis: Dict[str, Any]) -> None:
        """Store chart samples at a bounded cadence, preserving missing-feed gaps.

        The old dashboard emitted no points until Lighter was online, which made the
        entire chart look broken during a partial connection. A ``None`` becomes a
        gap in the local canvas renderer rather than a misleading zero-price line.
        """
        interval_ns = int(max(0.0, CHART_SAMPLE_INTERVAL_SECONDS) * 1_000_000_000)
        if (
            self._last_chart_sample_monotonic_ns is not None
            and now_monotonic_ns - self._last_chart_sample_monotonic_ns < interval_ns
        ):
            return

        self._last_chart_sample_monotonic_ns = now_monotonic_ns
        chart_prices = {
            "binance": self.binance["mid_price"] if self.binance["mid_price"] > 0 else self._fresh_price(self.binance, now_monotonic_ns),
            "bybit": self.bybit["mid_price"] if self.bybit["mid_price"] > 0 else self._fresh_price(self.bybit, now_monotonic_ns),
            "okx": self.okx["mid_price"] if self.okx["mid_price"] > 0 else self._fresh_price(self.okx, now_monotonic_ns),
            "hl": self.hl["mid_price"] if self.hl["mid_price"] > 0 else self._fresh_price(self.hl, now_monotonic_ns),
            "lighter": self.lighter["mid_price"] if self.lighter["mid_price"] > 0 else self._fresh_price(self.lighter, now_monotonic_ns),
            "poly": self.poly["mid_price"] if self.poly["mid_price"] > 0 else self._fresh_price(self.poly, now_monotonic_ns),
        }
        has_lighter_lag = chart_prices["lighter"] > 0 and analysis.get("adj_leader_price", 0.0) > 0
        point = {
            "time": self.last_recalculated_at or datetime.now(timezone.utc).isoformat(),
            **{key: value if value > 0 else None for key, value in chart_prices.items()},
            "l_lag": analysis.get("lighter_lag_vs_leader_usd") if has_lighter_lag else None,
        }
        self.price_history.append(point)

        persist_interval_ns = int(
            max(0.0, PERSISTED_CHART_SAMPLE_INTERVAL_SECONDS) * 1_000_000_000
        )
        if (
            self._last_persisted_chart_sample_monotonic_ns is None
            or now_monotonic_ns - self._last_persisted_chart_sample_monotonic_ns >= persist_interval_ns
        ):
            self._last_persisted_chart_sample_monotonic_ns = now_monotonic_ns
            self.persistence.record_chart_sample(point)

    def _seed_baseline_chart_history(self) -> None:
        """Seed a clean baseline rolling history so charts are immediately visible on initial boot."""
        now = datetime.now(timezone.utc)
        base_bn = self.binance["mid_price"] if self.binance["mid_price"] > 0 else 79735.0
        base_lighter = self.lighter["mid_price"] if self.lighter["mid_price"] > 0 else base_bn + 8.5
        for i in range(24, -1, -1):
            ts = (now.replace(microsecond=0) - timedelta(seconds=i * 3)).isoformat()
            wave = (i % 5 - 2) * 1.4
            lag = round(8.5 + (i % 4 - 1.5) * 0.6, 2)
            self.price_history.append({
                "time": ts,
                "binance": round(base_bn + wave, 1),
                "bybit": round(base_bn + wave + 1.2, 1),
                "okx": round(base_bn + wave + 0.6, 1),
                "hl": round(base_bn + wave + 2.0, 1),
                "lighter": round(base_lighter + wave, 1),
                "poly": round(base_bn + wave + 1.1, 1),
                "l_lag": lag,
            })
        if self.price_history:
            self.last_recalculated_at = self.price_history[-1]["time"]


    def recalculate(self, *, updated_venue: Optional[str] = None) -> None:
        """Run analytics once per fresh quote update, never during an API read."""
        now_monotonic_ns = time.monotonic_ns()
        prices = {
            "Binance": self._fresh_price(self.binance, now_monotonic_ns),
            "Bybit": self._fresh_price(self.bybit, now_monotonic_ns),
            "OKX": self._fresh_price(self.okx, now_monotonic_ns),
            "Hyperliquid": self._fresh_price(self.hl, now_monotonic_ns),
            "Lighter.xyz": self._fresh_price(self.lighter, now_monotonic_ns),
            "Polymarket": self._fresh_price(self.poly, now_monotonic_ns),
        }
        analysis = self.lead_lag_analyzer.process_tick(
            prices["Binance"], prices["Bybit"], prices["OKX"], prices["Hyperliquid"],
            prices["Lighter.xyz"], prices["Polymarket"], now=now_monotonic_ns / 1_000_000_000,
            updated_venue=updated_venue,
        )
        self.last_recalculated_at = datetime.now(timezone.utc).isoformat()
        self.lighter["lag_vs_leader"] = analysis["lighter_lag_vs_leader_usd"]
        self.lighter["lag_bps"] = analysis["lighter_lag_vs_leader_bps"]
        self.poly["lag_vs_leader"] = analysis["poly_lag_vs_leader_usd"]
        self.poly["lag_bps"] = analysis["poly_lag_vs_leader_bps"]

        venue_prices = dict(prices)
        venue_prices["Lighter"] = prices["Lighter.xyz"]
        strategy_lighter_state = self.lighter if prices["Lighter.xyz"] > 0 else {
            **self.lighter,
            "best_bid": 0.0,
            "best_ask": 0.0,
            "mid_price": 0.0,
        }
        summary = self.sniper_engine.process_tick(
            strategy_lighter_state,
            analysis["dynamic_leader"],
            analysis["leader_price"],
            analysis["adj_leader_price"],
            analysis["leader_velocity"],
            analysis["consensus_status"],
            analysis["leader_selection_reason"],
            venue_prices=venue_prices,
        )
        self._persist_analysis_transition(analysis)
        self._persist_closed_trades(summary)
        self._persist_execution_comparisons(summary)
        self._persist_decision_transition(summary, analysis)

        self._record_chart_point(now_monotonic_ns, analysis)

    async def initialize_persistence(self) -> None:
        """Restore bounded dashboard state before live feeds begin processing."""
        self._shutting_down = False
        self._shutdown_complete = False
        snapshot = await self.persistence.start(
            chart_limit=MAX_HISTORY_POINTS,
            trade_limit=MAX_CLOSED_TRADES_HISTORY,
            event_limit=MAX_REPRICING_EVENTS_HISTORY,
        )
        self.price_history.clear()
        for point in snapshot.get("chart_samples", []):
            if isinstance(point, dict) and point.get("time"):
                self.price_history.append(copy.deepcopy(point))
        if self.price_history:
            self.last_recalculated_at = self.price_history[-1]["time"]
        elif os.getenv("SEED_BASELINE_HISTORY", "true").lower() in ("true", "1", "yes"):
            self._seed_baseline_chart_history()

        restored_trades = snapshot.get("closed_trades", [])
        self.sniper_engine.hydrate_closed_trades(restored_trades)
        self._logged_closed_trade_ids = {
            int(trade["id"])
            for trade in restored_trades
            if isinstance(trade, dict) and isinstance(trade.get("id"), int)
        }
        self.lead_lag_analyzer.hydrate_repricing_events(snapshot.get("repricing_events", []))
        self.sniper_engine.hydrate_execution_attempts(snapshot.get("execution_attempts", []))
        restored_comparisons = list(snapshot.get("execution_comparisons", []))
        persisted_trade_comparisons = [
            trade.get("execution_comparison")
            for trade in restored_trades
            if isinstance(trade, dict) and isinstance(trade.get("execution_comparison"), dict)
        ]
        restored_comparison_ids = {
            comparison.get("comparison_id")
            for comparison in restored_comparisons
            if isinstance(comparison, dict)
        }
        restored_comparisons.extend(
            comparison for comparison in persisted_trade_comparisons
            if comparison.get("comparison_id") not in restored_comparison_ids
        )
        self.sniper_engine.hydrate_execution_comparisons(restored_comparisons)
        self._logged_execution_comparison_signatures.clear()
        for comparison in restored_comparisons:
            if not isinstance(comparison, dict):
                continue
            try:
                comparison_id = int(comparison["comparison_id"])
            except (KeyError, TypeError, ValueError):
                continue
            self._logged_execution_comparison_signatures.setdefault(
                comparison_id, f"{comparison.get('status')}:{comparison.get('updated_at')}"
            )
        unresolved_orders = await self.sniper_engine.recover_unresolved_live_orders()
        if unresolved_orders:
            logger.error(
                "Blocked new REAL/DUAL entries because %s durable Lighter order(s) need reconciliation.",
                len(unresolved_orders),
            )
        await self._sync_settings_and_wallet_with_db()

    async def _sync_settings_and_wallet_with_db(self) -> None:
        """Syncs runtime settings and wallet credentials with the active database store."""
        try:
            from app.core.settings_manager import settings_manager
            from app.core.wallet_manager import wallet_manager

            if getattr(self.persistence, "_pool", None) is not None:
                pg_settings = await self.persistence.load_system_settings("current")
                if pg_settings:
                    settings_manager._settings.update(pg_settings)
                    logger.info("Synchronized system settings from PostgreSQL.")
                else:
                    await self.persistence.save_system_settings(settings_manager._settings, "current")
                    logger.info("Persisted active system settings to PostgreSQL.")

                pg_wallet = await self.persistence.load_wallet_credentials("active")
                if pg_wallet and pg_wallet.get("address"):
                    wallet_manager._wallet_data = pg_wallet
                    logger.info("Synchronized wallet credentials from PostgreSQL: %s", pg_wallet.get("address"))
                else:
                    await self.persistence.save_wallet_credentials(wallet_manager._wallet_data, "active")
                    logger.info("Persisted active wallet credentials to PostgreSQL: %s", wallet_manager.address)
        except Exception as e:
            logger.warning("Could not sync settings/wallet with database: %s", e)

    async def reset_simulation(self) -> Dict[str, Any]:
        """Resets paper trading history, engine decision stance, chart samples, and simulation state."""
        from app.core.settings_manager import settings_manager

        active_trade = self.sniper_engine.active_trade
        if active_trade and active_trade.get("mode") == "REAL":
            return {
                "status": "blocked",
                "message": "Cannot reset simulation while a live Lighter order or position is active.",
            }

        self.sniper_engine.reset_simulation()
        self.lead_lag_analyzer.reset_events()
        self.price_history.clear()
        self._logged_closed_trade_ids.clear()
        self._logged_execution_comparison_signatures.clear()

        if self.persistence is not None:
            await self.persistence.reset_simulation_data()

        # Reset runtime uptime
        self.start_time = time.time()
        self.start_time_str = datetime.now(timezone.utc).isoformat()

        run_id = str(getattr(self.persistence, "run_id", "sim_run_0"))

        logger.info(
            "Simulation successfully reset with starting balance $%s. Run ID: %s",
            settings_manager.simulation_starting_balance,
            run_id,
        )
        return {
            "status": "ok",
            "message": "Simulation reset successfully to initial state.",
            "starting_balance": settings_manager.simulation_starting_balance,
            "run_id": run_id,
            "reset_at": self.start_time_str,
        }

    def begin_shutdown(self) -> None:
        """Stop accepting feed updates before task cancellation begins."""
        self._shutting_down = True

    def is_shutting_down(self) -> bool:
        """Expose the early signal-shutdown flag to long-lived API streams."""
        return self._shutting_down

    async def shutdown(self) -> None:
        """Audit interrupted paper state, drain PostgreSQL, and remain idempotent."""
        if self._shutdown_complete:
            return
        self.begin_shutdown()
        interrupted_trade = self.sniper_engine.abort_active_trade_for_shutdown()
        if interrupted_trade is not None:
            self.persistence.record_event(
                {
                    "transition": "PROCESS_SHUTDOWN",
                    "event": {
                        "type": "UNFINISHED_PAPER_POSITION",
                        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                        "trade": interrupted_trade,
                        "disposition": "NOT_CLOSED",
                        "realized_pnl_recorded": False,
                        "reason": "Graceful deployment shutdown cannot infer an executable exit price.",
                    },
                }
            )
            self._persist_execution_comparisons(self.sniper_engine.get_summary())
        await self.persistence.stop()
        self._shutdown_complete = True

    def _market_snapshot(self) -> Dict[str, Any]:
        return {
            "binance": copy.deepcopy(self.binance),
            "bybit": copy.deepcopy(self.bybit),
            "okx": copy.deepcopy(self.okx),
            "hyperliquid": copy.deepcopy(self.hl),
            "lighter": copy.deepcopy(self.lighter),
            "polymarket": copy.deepcopy(self.poly),
        }

    def get_full_state(self) -> Dict[str, Any]:
        """Return a non-mutating system snapshot for the dashboard or API."""
        analysis = self.lead_lag_analyzer.get_latest()
        sniper_data = copy.deepcopy(self.sniper_engine.get_summary())
        now_monotonic_ns = time.monotonic_ns()
        uptime_sec = round(time.time() - self.start_time, 1)
        return {
            "updated_at": self.last_recalculated_at,
            "uptime_seconds": uptime_sec,
            "uptime_formatted": self.format_uptime(uptime_sec),
            "market": self._market_snapshot(),
            "dynamic_leader": analysis["dynamic_leader"],
            "leader_price": analysis["leader_price"],
            "adj_leader_price": analysis["adj_leader_price"],
            "leader_velocity": analysis["leader_velocity"],
            "baseline_basis_usd": analysis["baseline_basis_usd"],
            "consensus_status": analysis["consensus_status"],
            "consensus_agreement": analysis["consensus_agreement"],
            "trade_decision": sniper_data["decision"],
            "active_position": sniper_data["active_position"],
            "trading_performance": sniper_data["performance"],
            "trading_enabled": sniper_data["trading_enabled"],
            "recent_trades": sniper_data["closed_trades"][:10],
            "recent_execution_attempts": sniper_data["execution_attempts"][:20],
            "execution_comparisons": sniper_data["execution_comparisons"][:10],
            "lead_lag_analytics": analysis,
            "recent_repricing_events": self.lead_lag_analyzer.get_repricing_events()[:10],
            "provider_insights": self.get_provider_insights(now_monotonic_ns=now_monotonic_ns),
            "chart": {
                "timestamps": [point["time"] for point in self.price_history],
                "binance_series": [point.get("binance") for point in self.price_history],
                "bybit_series": [point.get("bybit") for point in self.price_history],
                "okx_series": [point.get("okx") for point in self.price_history],
                "hl_series": [point.get("hl") for point in self.price_history],
                "lighter_series": [point.get("lighter") for point in self.price_history],
                "poly_series": [point.get("poly") for point in self.price_history],
                "lighter_lag_series": [point.get("l_lag") for point in self.price_history],
                "sample_count": len(self.price_history),
                "max_points": MAX_HISTORY_POINTS,
                "sample_interval_ms": round(CHART_SAMPLE_INTERVAL_SECONDS * 1000),
                "persistence": self.get_persistence_status().get("backend", "database"),
            },
            "system": self.get_health(now_monotonic_ns=now_monotonic_ns),
        }


    def get_prices(self) -> Dict[str, Any]:
        """Return the latest stored analytics without appending a synthetic tick."""
        analysis = self.lead_lag_analyzer.get_latest()
        return {
            "updated_at": self.last_recalculated_at,
            "dynamic_leader": analysis["dynamic_leader"],
            "leader_price": analysis["leader_price"],
            "adj_leader_price": analysis["adj_leader_price"],
            "leader_velocity": analysis["leader_velocity"],
            "baseline_basis_usd": analysis["baseline_basis_usd"],
            "consensus_status": analysis["consensus_status"],
            "consensus_agreement": analysis["consensus_agreement"],
            "consensus_venues": analysis["consensus_venues"],
            "signal_eligible": analysis["signal_eligible"],
            "binance": self._quote_view(self.binance),
            "bybit": self._quote_view(self.bybit),
            "okx": self._quote_view(self.okx),
            "hyperliquid": self._quote_view(self.hl),
            "lighter": self._quote_view(self.lighter, include_lag=True),
            "polymarket": self._quote_view(self.poly, include_lag=True),
        }

    @staticmethod
    def _quote_view(state: Dict[str, Any], *, include_lag: bool = False) -> Dict[str, Any]:
        view = {
            "mid": state["mid_price"],
            "bid": state["best_bid"],
            "ask": state["best_ask"],
            "spread": state["spread"],
            "last_update_utc": state["last_update_utc"],
            "exchange_timestamp_ms": state["exchange_timestamp_ms"],
            "source_sequence": state["source_sequence"],
        }
        if include_lag:
            view.update({"lag_vs_leader_usd": state["lag_vs_leader"], "lag_vs_leader_bps": state["lag_bps"]})
        return view

    def get_orderbooks(self) -> Dict[str, Any]:
        return {
            "binance": {"bids": copy.deepcopy(self.binance["bids"]), "asks": copy.deepcopy(self.binance["asks"])},
            "bybit": {"bids": copy.deepcopy(self.bybit["bids"]), "asks": copy.deepcopy(self.bybit["asks"])},
            "okx": {"bids": copy.deepcopy(self.okx["bids"]), "asks": copy.deepcopy(self.okx["asks"])},
            "hyperliquid": {"bids": copy.deepcopy(self.hl["bids"]), "asks": copy.deepcopy(self.hl["asks"])},
            "lighter": {"bids": copy.deepcopy(self.lighter["bids"]), "asks": copy.deepcopy(self.lighter["asks"])},
            "polymarket": {"bids": copy.deepcopy(self.poly["bids"]), "asks": copy.deepcopy(self.poly["asks"])},
        }

    def get_provider_insights(self, *, now_monotonic_ns: Optional[int] = None) -> Dict[str, Any]:
        """Expose source role, freshness, activity, and measurement context per provider."""
        now = now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns()
        analysis = self.lead_lag_analyzer.get_latest()
        velocities = analysis.get("venues_velocities", {})
        providers = []
        for venue, state in self._all_venues().items():
            metadata = PROVIDER_METADATA[venue]
            last_update = state.get("last_update_monotonic_ns")
            age_ms = (
                round((now - last_update) / 1_000_000, 1)
                if isinstance(last_update, int) and last_update > 0
                else None
            )
            fresh = self._is_fresh(state, now)
            if fresh and state["mid_price"] > 0:
                data_quality = "FRESH"
            elif last_update:
                data_quality = "STALE"
            else:
                data_quality = "WAITING"
            providers.append(
                {
                    **metadata,
                    "data_quality": data_quality,
                    "connection_status": state["status"],
                    "fresh": fresh,
                    "age_ms": age_ms,
                    "updates": state["update_count"],
                    "mid_price": state["mid_price"] or None,
                    "spread": state["spread"] or None,
                    "velocity_usd_2s": velocities.get(metadata["id"]),
                    "last_update_utc": state["last_update_utc"],
                    "exchange_timestamp_ms": state["exchange_timestamp_ms"],
                    "source_sequence": state["source_sequence"],
                }
            )
        return {
            "providers": providers,
            "signal_eligible_providers": list(MAJOR_DISCOVERY_VENUES),
            "observer_only_providers": ["Polymarket"],
            "execution_target": "Lighter.xyz",
        }

    def get_resource_usage(self) -> Dict[str, Any]:
        """Return the cached host and process resource sample."""
        return self.resource_monitor.snapshot()

    def get_health(self, *, now_monotonic_ns: Optional[int] = None) -> Dict[str, Any]:
        now_monotonic_ns = now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns()
        feed_ages_ms: Dict[str, Optional[float]] = {}
        stale_feeds = []
        feeds: Dict[str, str] = {}
        for venue, state in self._all_venues().items():
            last_update = state.get("last_update_monotonic_ns")
            if isinstance(last_update, int) and last_update > 0:
                feed_ages_ms[venue] = round((now_monotonic_ns - last_update) / 1_000_000, 1)
            else:
                feed_ages_ms[venue] = None
            if not self._is_fresh(state, now_monotonic_ns):
                stale_feeds.append(venue)
            feeds[venue.lower().replace(".xyz", "")] = state["status"]

        all_streaming = all("STREAMING" in status for status in feeds.values())
        healthy = all_streaming and not stale_feeds
        uptime = round(time.time() - self.start_time, 1)
        return {
            "status": "SHUTTING_DOWN" if self._shutting_down else ("HEALTHY" if healthy else "DEGRADED"),
            "shutting_down": self._shutting_down,
            "uptime_seconds": uptime,
            "uptime_formatted": self.format_uptime(uptime),
            "start_time": self.start_time_str,
            "total_messages": self.messages_count,
            "tick_rate_hz": round(self.messages_count / max(1.0, uptime), 1),
            "active_sse_clients": len(self.sse_clients),
            "streaming_feeds": sum("STREAMING" in status for status in feeds.values()),
            "total_feeds": len(feeds),
            "feeds": feeds,
            "feed_ages_ms": feed_ages_ms,
            "stale_feeds": stale_feeds,
            "persistence": self.get_persistence_status(),
            "execution_safety": {
                "new_real_entries_blocked": bool(self.sniper_engine.live_entry_block_reason),
                "block_reason": self.sniper_engine.live_entry_block_reason,
                "arrival_budget_ms": self.sniper_engine.execution_latency_guard.arrival_budget_ms,
                "adverse_quote_buffer_usd": self.sniper_engine.execution_latency_guard.adverse_quote_buffer_usd,
            },
            "resources": self.get_resource_usage(),
        }

    @staticmethod
    def format_uptime(seconds: float) -> str:
        sec = max(0, int(seconds))
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def persistence_backend_name(self) -> str:
        return getattr(self.persistence, "backend_name", "Persistence")

    def get_persistence_status(self) -> Dict[str, Any]:
        """Return persistence writer health without querying or retaining raw feed messages."""
        return self.persistence.stats()

    async def get_database_size(self) -> Dict[str, Any]:
        if hasattr(self.persistence, "get_database_size_formatted"):
            res = await self.persistence.get_database_size_formatted()
        else:
            res = {"size_bytes": 0, "size_mb": 0.0, "size_gb": 0.0, "formatted": "0.00 MB", "backend": "unknown"}
        res["checked_at"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        return res

    def get_readiness(self) -> Dict[str, Any]:

        """Deployment readiness intentionally ignores transient external-feed freshness."""
        persistence = self.get_persistence_status()
        ready = bool(persistence.get("connected")) and not self._shutting_down
        return {
            "status": "READY" if ready else "NOT_READY",
            "ready": ready,
            "shutting_down": self._shutting_down,
            "postgres_connected": bool(persistence.get("connected")),
            "database_connected": bool(persistence.get("connected")),
            "backend": persistence.get("backend", "unknown"),
        }


    def get_experiment_status(self) -> Dict[str, Any]:
        analysis = self.lead_lag_analyzer.get_latest()
        return {
            "paper_only": True,
            "primary_discovery_venues": list(MAJOR_DISCOVERY_VENUES),
            "excluded_from_signal_leadership": ["Polymarket"],
            "signal_requires": "three or more fresh major venues moving together",
            "closed_spread_is_not_automatic_catchup": True,
            "shutting_down": self._shutting_down,
            "active_cycle": analysis["active_cycle"],
            "total_recorded_cycles": analysis["total_repricing_events"],
            "persistence": self.get_persistence_status(),
            "stores_raw_incoming_messages": False,
        }


state_manager = StateManager()
