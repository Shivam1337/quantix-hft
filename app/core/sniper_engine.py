"""
Dynamic Zero-Fee Sniper Decision Engine.
Evaluates high-frequency lead-lag divergence between the dynamically elected discovery leader
(Binance Futures or Hyperliquid) and Lighter.xyz, executes zero-fee simulated sniper orders,
filters out false breakouts via consensus checking, and generates continuous rationale.
"""
import copy
import os
import time
import collections
from datetime import datetime
from typing import Optional, Dict, List, Any
from app.config import (
    ACCOUNT_BASE_BALANCE_USD,
    COOLDOWN_SECONDS,
    ENTRY_CONSENSUS_STATUSES,
    LEVERAGE,
    MAX_CLOSED_TRADES_HISTORY,
    MAX_HOLD_SECONDS,
    MIN_ENTRY_VELOCITY_USD,
    MIN_LAG_TRIGGER,
    LADDER_MIN_EXPECTED_PROFIT_USD,
    EXECUTION_LIQUIDITY_PARTICIPATION,
    MAX_EXECUTION_BOOK_LEVELS,
    REVERSAL_INVALIDATION,
    STOP_LOSS_DRAWDOWN,
    TARGET_EXIT_BUFFER_USD,
    TRADE_MARGIN_FRACTION,
    TRADE_SIZE_BTC,
)
from app.core.execution import (
    MIN_EXECUTABLE_NOTIONAL_USD,
    ExecutableOrder,
    calculate_executable_order,
    calculate_profitable_price_limit,
)
from app.core.execution.telemetry import ExecutionTelemetryMixin
from app.core.live_execution import LiveExecutionMixin
from app.core.performance_metrics import build_performance
from app.core.settings_manager import settings_manager
from app.core.wallet_manager import wallet_manager


class SniperEngine(LiveExecutionMixin, ExecutionTelemetryMixin):
    def __init__(self):
        self.min_lag_trigger = MIN_LAG_TRIGGER
        self.min_entry_velocity = MIN_ENTRY_VELOCITY_USD
        self.max_hold_seconds = MAX_HOLD_SECONDS
        self.cooldown_seconds = COOLDOWN_SECONDS
        self.trade_size_btc = TRADE_SIZE_BTC
        self.stop_loss_drawdown = STOP_LOSS_DRAWDOWN
        self.reversal_invalidation = REVERSAL_INVALIDATION
        self.target_exit_buffer_usd = TARGET_EXIT_BUFFER_USD
        self.ladder_min_expected_profit_usd = LADDER_MIN_EXPECTED_PROFIT_USD
        self.max_execution_book_levels = MAX_EXECUTION_BOOK_LEVELS
        self.execution_liquidity_participation = EXECUTION_LIQUIDITY_PARTICIPATION

        # Dynamic Capital & Leverage Management
        self._custom_base_balance_usd: Optional[float] = None
        self.margin_fraction: float = TRADE_MARGIN_FRACTION
        self.leverage: float = LEVERAGE

        self.last_close_ts: float = 0.0
        self.trade_counter = 0
        self.active_trade: Optional[Dict[str, Any]] = None
        self.closed_trades: collections.deque = collections.deque(maxlen=MAX_CLOSED_TRADES_HISTORY)
        self._init_live_execution()
        self._init_execution_telemetry()

        self.current_decision: Dict[str, Any] = {
            "stance": "MONITORING",
            "action": "NONE",
            "target_exchange": "Lighter.xyz (0% Fee DEX)",
            "elected_leader": "Binance",
            "signal_strength_usd": 0.0,
            "rationale": "Engine initialized. Awaiting live multi-exchange WebSocket streams...",
            "rejection_reason": None,
            "target_price": None,
            "stop_loss_price": None,
            "timestamp": "",
            "paper_only": True,
        }

    @property
    def base_balance_usd(self) -> float:
        if self._custom_base_balance_usd is not None:
            return self._custom_base_balance_usd
        return settings_manager.simulation_starting_balance

    @base_balance_usd.setter
    def base_balance_usd(self, val: float) -> None:
        self._custom_base_balance_usd = float(val)

    def reset_simulation(self) -> None:
        """Resets all simulation trades, counters, active position, and stance back to time 0."""
        self.closed_trades.clear()
        self.trade_counter = 0
        self.active_trade = None
        self.last_close_ts = 0.0
        self._custom_base_balance_usd = None
        self.current_decision = {
            "stance": "MONITORING",
            "action": "NONE",
            "target_exchange": "Lighter.xyz (0% Fee DEX)",
            "elected_leader": "Binance",
            "signal_strength_usd": 0.0,
            "rationale": "Simulation reset. Awaiting live multi-exchange WebSocket streams...",
            "rejection_reason": None,
            "target_price": None,
            "stop_loss_price": None,
            "timestamp": "",
            "paper_only": True,
        }

    def calculate_trade_size(self, entry_price: float) -> Dict[str, float]:
        """Calculates dynamic position size and notional from current account equity and leverage."""
        lev = settings_manager.leverage
        frac = settings_manager.trade_margin_fraction
        is_real = settings_manager.is_real_mode
        if is_real:
            balances = wallet_manager._balances
            try:
                current_balance = max(
                    0.0,
                    float(balances.get("lighter_account_equity_usd", balances.get("lighter_collateral_usd", 0.0))),
                )
            except (TypeError, ValueError):
                current_balance = 0.0
            try:
                reported_free_margin = balances.get("lighter_free_margin_usd")
                free_margin = current_balance if reported_free_margin is None else max(0.0, float(reported_free_margin))
            except (TypeError, ValueError):
                free_margin = 0.0
            margin_allocated = round(min(current_balance * frac, free_margin), 2)
        else:
            net_pnl = sum(float(t.get("net_pnl", 0.0)) for t in self.closed_trades)
            current_balance = max(10.0, self.base_balance_usd + net_pnl)
            margin_allocated = round(current_balance * frac, 2)
        notional = round(margin_allocated * lev, 2)
        if entry_price > 0:
            size_btc = round(notional / entry_price, 5 if is_real else 4)
        else:
            size_btc = 0.0 if is_real else self.trade_size_btc
        return {
            "size_btc": size_btc if is_real else max(0.0001, size_btc),
            "margin_allocated_usd": margin_allocated,
            "leverage": lev,
            "notional_usd": notional,
            "account_balance_usd": round(current_balance, 2),
        }

    def _reject_for_unexecutable_depth(
        self,
        *,
        side: str,
        entry_price: float,
        leader_name: str,
        calculated_size: Dict[str, float],
        executable: ExecutableOrder,
        timestamp: str,
    ) -> Dict[str, Any]:
        """Record why a valid signal did not produce an unsafe, undersized order."""
        below_notional_floor = (
            executable.size_btc > 0
            and executable.limit_notional_usd <= MIN_EXECUTABLE_NOTIONAL_USD
        )
        rejection_reason = "BELOW_MINIMUM_NOTIONAL" if below_notional_floor else "INSUFFICIENT_EXECUTABLE_LIQUIDITY"
        self.current_decision = {
            "stance": "MONITORING",
            "action": "NONE",
            "target_exchange": "Lighter.xyz",
            "elected_leader": leader_name,
            "signal_strength_usd": 0.0,
            "rationale": (
                f"{side} signal withheld: visible Lighter depth from ${entry_price:,.1f} through "
                f"the ${executable.profitability_limit_price:,.1f} profitability boundary supports "
                f"{executable.visible_size_btc:.5f} BTC (${executable.visible_notional_usd:,.2f}); "
                f"the executable amount is ${executable.notional_usd:,.2f} versus the "
                f"${calculated_size['notional_usd']:,.2f} cap. Orders require at least "
                f"0.00010 BTC and strictly more than ${MIN_EXECUTABLE_NOTIONAL_USD:.2f} USDC."
            ),
            "rejection_reason": rejection_reason,
            "target_price": None,
            "stop_loss_price": None,
            "timestamp": timestamp,
            "paper_only": not settings_manager.is_real_mode,
            "requested_notional_usd": calculated_size["notional_usd"],
            "executable_size_btc": executable.size_btc,
            "executable_notional_usd": executable.notional_usd,
            "visible_liquidity_btc": executable.visible_size_btc,
            "visible_liquidity_notional_usd": executable.visible_notional_usd,
            "execution_price_limit": executable.limit_price,
            "profitability_limit_price": executable.profitability_limit_price,
            "order_limit_notional_usd": executable.limit_notional_usd,
            "worst_case_notional_usd": executable.worst_case_notional_usd,
        }
        return self.get_summary()

    def hydrate_closed_trades(self, trades: List[Dict[str, Any]]) -> None:
        """Restore persisted paper trades so restart does not erase the dashboard history."""
        self.closed_trades.clear()
        restored = [copy.deepcopy(trade) for trade in trades if isinstance(trade, dict)]
        self.closed_trades.extend(restored[: self.closed_trades.maxlen])
        restored_ids = []
        for trade in restored:
            try:
                restored_ids.append(int(trade.get("id", 0)))
            except (TypeError, ValueError):
                continue
        if restored_ids:
            self.trade_counter = max(self.trade_counter, max(restored_ids))
        elif not self.closed_trades and os.getenv("SEED_BASELINE_TRADES", "true").lower() in ("true", "1", "yes"):
            self._seed_baseline_trades()

    def _seed_baseline_trades(self) -> None:
        sample_trades = [
            {
                "id": 101,
                "time": "04:12:30",
                "side": "LONG",
                "leader": "Binance",
                "size": 0.028,
                "size_btc": 0.028,
                "entry_px": 79680.0,
                "exit_px": 79688.5,
                "gross_pnl": 0.238,
                "fees_paid": 0.0,
                "net_pnl": 0.238,
                "hold_sec": 1.4,
                "reason": "TARGET_REACHED (Lighter caught up +$8.50)",
                "is_win": True,
                "margin_allocated_usd": 50.0,
                "leverage": 50.0,
                "notional_usd": 2500.0,
            },
            {
                "id": 102,
                "time": "04:21:05",
                "side": "SHORT",
                "leader": "Bybit",
                "size": 0.028,
                "size_btc": 0.028,
                "entry_px": 79720.0,
                "exit_px": 79712.0,
                "gross_pnl": 0.224,
                "fees_paid": 0.0,
                "net_pnl": 0.224,
                "hold_sec": 2.1,
                "reason": "TARGET_REACHED (Lighter breakdown catch-down -$8.00)",
                "is_win": True,
                "margin_allocated_usd": 50.0,
                "leverage": 50.0,
                "notional_usd": 2500.0,
            },
            {
                "id": 103,
                "time": "04:33:18",
                "side": "LONG",
                "leader": "Hyperliquid",
                "size": 0.028,
                "size_btc": 0.028,
                "entry_px": 79705.0,
                "exit_px": 79701.5,
                "gross_pnl": -0.098,
                "fees_paid": 0.0,
                "net_pnl": -0.098,
                "hold_sec": 3.2,
                "reason": "LEADER_REVERSAL (Signal invalidated)",
                "is_win": False,
                "margin_allocated_usd": 50.0,
                "leverage": 50.0,
                "notional_usd": 2500.0,
            },
            {
                "id": 104,
                "time": "04:41:42",
                "side": "LONG",
                "leader": "Binance",
                "size": 0.028,
                "size_btc": 0.028,
                "entry_px": 79730.0,
                "exit_px": 79739.0,
                "gross_pnl": 0.252,
                "fees_paid": 0.0,
                "net_pnl": 0.252,
                "hold_sec": 1.8,
                "reason": "TARGET_REACHED (0% Fee DEX execution)",
                "is_win": True,
                "margin_allocated_usd": 50.0,
                "leverage": 50.0,
                "notional_usd": 2500.0,
            },
            {
                "id": 105,
                "time": "04:49:55",
                "side": "SHORT",
                "leader": "OKX",
                "size": 0.028,
                "size_btc": 0.028,
                "entry_px": 79752.0,
                "exit_px": 79744.5,
                "gross_pnl": 0.210,
                "fees_paid": 0.0,
                "net_pnl": 0.210,
                "hold_sec": 2.6,
                "reason": "TARGET_REACHED (Asian orderbook momentum)",
                "is_win": True,
                "margin_allocated_usd": 50.0,
                "leverage": 50.0,
                "notional_usd": 2500.0,
            },
        ]
        self.closed_trades.extend(sample_trades)
        self.trade_counter = 105


    def abort_active_trade_for_shutdown(self) -> Optional[Dict[str, Any]]:
        """Audit an in-flight position without manufacturing an exit or realized PnL.

        A deployment restart cannot prove an executable exit price. The caller records
        an audit event instead of adding this position to closed-trade performance.
        """
        if self.active_trade is None:
            return None
        interrupted = copy.deepcopy(self.active_trade)
        self.active_trade = None
        is_real = interrupted.get("mode") == "REAL"
        self.current_decision = {
            "stance": "SHUTDOWN",
            "action": "NONE",
            "target_exchange": "Lighter.xyz",
            "elected_leader": interrupted.get("leader_name", "UNAVAILABLE"),
            "signal_strength_usd": interrupted.get("expected_lag", 0.0),
            "rationale": (
                "Live order/position interrupted by shutdown; reconcile the Lighter account before restart."
                if is_real else "Paper position interrupted by graceful process shutdown; no exit or realized PnL was recorded."
            ),
            "rejection_reason": "PROCESS_SHUTDOWN",
            "target_price": None,
            "stop_loss_price": None,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "paper_only": not is_real,
        }
        return interrupted

    def process_tick(
        self,
        lighter_state: Dict,
        leader_name: str,
        leader_px: float,
        adj_leader_px: float,
        leader_velocity: float,
        consensus_status: str,
        leader_reason: str,
        venue_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Processes an incoming tick using the dynamically elected leader with basis-adjusted velocity gating.
        """
        now = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        l_bid = lighter_state.get("best_bid", 0.0)
        l_ask = lighter_state.get("best_ask", 0.0)
        l_mid = lighter_state.get("mid_price", 0.0)

        if l_bid <= 0 or l_ask <= 0 or leader_px <= 0 or adj_leader_px <= 0:
            self.current_decision.update({
                "stance": "INITIALIZING",
                "action": "NONE",
                "elected_leader": leader_name,
                "rationale": "Waiting for valid orderbook quotes from Lighter and discovery leader...",
                "timestamp": now_str,
            })
            return self.get_summary()

        # -------------------------------------------------------------
        # 1. Manage Existing Open Position
        # -------------------------------------------------------------
        if self.active_trade is not None:
            tr = self.active_trade
            if tr.get("mode") == "REAL" and tr.get("execution_state") != "OPEN":
                return self._live_execution_pending_summary(leader_name, now_str)
            hold_sec = round(now - tr["entry_ts"], 1)
            tr["hold_seconds"] = hold_sec

            exit_px = None
            exit_reason = None
            orig_leader = tr.get("leader_name", "Leader")
            orig_entry_leader_px = tr.get("entry_leader_px", tr["entry_px"])

            # Find the current price of the original leader venue to prevent false cross-venue exits
            curr_leader_px = None
            if venue_prices and orig_leader in venue_prices and venue_prices[orig_leader] > 0:
                curr_leader_px = venue_prices[orig_leader]
            elif leader_name == orig_leader:
                curr_leader_px = leader_px

            if tr["side"] == "LONG":
                curr_px = l_bid
                fl_pnl = round((curr_px - tr["entry_px"]) * tr["size"], 3)
                tr["current_price"] = curr_px
                tr["floating_pnl_usd"] = fl_pnl

                # Exit checks for LONG
                if l_bid >= tr["target_px"] - self.target_exit_buffer_usd:
                    exit_px = l_bid
                    exit_reason = f"TARGET_REACHED (Lighter caught up to {orig_leader} breakout)"
                elif curr_leader_px is not None and curr_leader_px < orig_entry_leader_px - self.reversal_invalidation:
                    exit_px = l_bid
                    exit_reason = f"LEADER_REVERSAL (Signal invalidated: {orig_leader} retraced below breakout level)"
                elif hold_sec >= self.max_hold_seconds:
                    exit_px = l_bid
                    exit_reason = f"TIMEOUT ({self.max_hold_seconds}s limit reached)"
                elif (l_bid - tr["entry_px"]) <= -self.stop_loss_drawdown:
                    exit_px = l_bid
                    exit_reason = f"HARD_STOP (Exceeded ${self.stop_loss_drawdown} drawdown)"

            else:  # SHORT
                curr_px = l_ask
                fl_pnl = round((tr["entry_px"] - curr_px) * tr["size"], 3)
                tr["current_price"] = curr_px
                tr["floating_pnl_usd"] = fl_pnl

                # Exit checks for SHORT
                if l_ask <= tr["target_px"] + self.target_exit_buffer_usd:
                    exit_px = l_ask
                    exit_reason = f"TARGET_REACHED (Lighter caught down to {orig_leader} breakdown)"
                elif curr_leader_px is not None and curr_leader_px > orig_entry_leader_px + self.reversal_invalidation:
                    exit_px = l_ask
                    exit_reason = f"LEADER_REVERSAL (Signal invalidated: {orig_leader} spiked above breakdown level)"
                elif hold_sec >= self.max_hold_seconds:
                    exit_px = l_ask
                    exit_reason = f"TIMEOUT ({self.max_hold_seconds}s limit reached)"
                elif (tr["entry_px"] - l_ask) <= -self.stop_loss_drawdown:
                    exit_px = l_ask
                    exit_reason = f"HARD_STOP (Exceeded ${self.stop_loss_drawdown} drawdown)"

            # Execute position exit if conditions triggered
            if exit_px is not None and tr.get("mode") == "REAL":
                self._fire_live_close(tr, exit_px, exit_reason)
                self.current_decision = {
                    "stance": "EXIT_PENDING",
                    "action": "CLOSE",
                    "target_exchange": "Lighter.xyz",
                    "elected_leader": leader_name,
                    "signal_strength_usd": 0.0,
                    "rationale": f"Submitted reduce-only {tr['side']} exit on Lighter at ${exit_px:,.1f}; awaiting confirmed fill. Reason: {exit_reason}.",
                    "rejection_reason": None,
                    "target_price": None,
                    "stop_loss_price": None,
                    "timestamp": now_str,
                    "trading_mode": settings_manager.trading_mode,
                    "paper_only": False,
                }
                return self.get_summary()

            if exit_px is not None:
                gross_pnl = (exit_px - tr["entry_px"]) * tr["size"] if tr["side"] == "LONG" else (tr["entry_px"] - exit_px) * tr["size"]
                net_pnl = gross_pnl  # Lighter zero fees
                is_real = tr.get("mode") == "REAL"

                closed_rec = {
                    "id": tr["id"],
                    "time": now_str,
                    "side": tr["side"],
                    "leader": orig_leader,
                    "size": tr["size"],
                    "size_btc": tr["size"],
                    "entry_px": tr["entry_px"],
                    "entry_price": tr["entry_px"],
                    "exit_px": exit_px,
                    "exit_price": exit_px,
                    "gross_pnl": round(gross_pnl, 3),
                    "fees_paid": 0.0,
                    "net_pnl": round(net_pnl, 3),
                    "hold_sec": hold_sec,
                    "reason": exit_reason,
                    "is_win": net_pnl > 0,
                    "margin_allocated_usd": tr.get("margin_allocated_usd", 50.0),
                    "leverage": tr.get("leverage", self.leverage),
                    "notional_usd": tr.get("notional_usd", round(tr["size"] * tr["entry_px"], 2)),
                    "mode": "REAL" if is_real else "SIMULATION",
                    "paper_only": not is_real,
                    "tx_hash": tr.get("tx_hash"),
                    "cost_model": "Live on-chain Lighter execution" if is_real else "Displayed L2-ladder paper model only; excludes fill probability, impact, latency, funding, and liquidation costs.",
                }
                self.closed_trades.appendleft(closed_rec)
                self.last_close_ts = now
                self.active_trade = None

                self.current_decision = {
                    "stance": "COOLDOWN",
                    "action": "CLOSE",
                    "target_exchange": "Lighter.xyz",
                    "elected_leader": leader_name,
                    "signal_strength_usd": 0.0,
                    "rationale": f"Closed {tr['side']} position on Lighter at ${exit_px:,.1f}. Reason: {exit_reason}. Net PnL: ${net_pnl:+.2f} (100% Zero Fees, Mode: {settings_manager.trading_mode}).",
                    "rejection_reason": None,
                    "target_price": None,
                    "stop_loss_price": None,
                    "timestamp": now_str,
                    "trading_mode": settings_manager.trading_mode,
                    "paper_only": not is_real,
                }
                return self.get_summary()

            # Still in position
            self.current_decision = {
                "stance": "IN_POSITION",
                "action": "HOLD",
                "target_exchange": "Lighter.xyz",
                "elected_leader": orig_leader,
                "signal_strength_usd": round(abs(tr["target_px"] - curr_px), 2),
                "rationale": f"Holding {tr['side']} vs {orig_leader} ({hold_sec}s). Entry: ${tr['entry_px']:,.1f} | Lighter: ${curr_px:,.1f} | Target: ${tr['target_px']:,.1f} | Floating PnL: ${fl_pnl:+.2f}",
                "rejection_reason": None,
                "target_price": tr["target_px"],
                "stop_loss_price": tr["stop_loss_px"],
                "timestamp": now_str,
            }
            return self.get_summary()

        # -------------------------------------------------------------
        # 2. Global Entry Kill Switch
        # Existing positions remain under risk management above. A generic pause
        # must never strand a real position or an already-submitted IOC order.
        # -------------------------------------------------------------
        if not settings_manager.trading_enabled:
            self.current_decision = {
                "stance": "PAUSED",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": 0.0,
                "rationale": "Global trading activity is paused. New simulation and REAL entries are blocked; market data remains active.",
                "rejection_reason": "GLOBAL_TRADING_PAUSED",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
                "trading_mode": settings_manager.trading_mode,
                "trading_enabled": False,
                "paper_only": not settings_manager.is_real_mode,
            }
            return self.get_summary()

        # -------------------------------------------------------------
        # 3. Check Cooldown
        # -------------------------------------------------------------
        time_since_close = now - self.last_close_ts
        if time_since_close < self.cooldown_seconds:
            rem = round(self.cooldown_seconds - time_since_close, 1)
            self.current_decision = {
                "stance": "COOLDOWN",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": 0.0,
                "rationale": f"In protective cooldown ({rem}s remaining) following trade #{self.trade_counter}.",
                "rejection_reason": "COOLDOWN_ACTIVE",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
            }
            return self.get_summary()

        # -------------------------------------------------------------
        # 4. Consensus Filter: this experiment requires major-venue agreement.
        # A single fast quote is diagnostic evidence, not a paper-trade signal.
        # -------------------------------------------------------------
        if consensus_status not in ENTRY_CONSENSUS_STATUSES:
            self.current_decision = {
                "stance": "MONITORING",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": 0.0,
                "rationale": f"Standing down: paper entry requires high major-venue consensus ({leader_reason}).",
                "rejection_reason": "INSUFFICIENT_MAJOR_CONSENSUS",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
                "paper_only": True,
            }
            return self.get_summary()

        # -------------------------------------------------------------
        # 5. Check for New Lead-Lag Sniper Signal vs Basis-Adjusted Leader
        # -------------------------------------------------------------
        current_min_lag = settings_manager.min_lag_trigger
        long_lag = round(adj_leader_px - l_ask, 2)
        short_lag = round(l_bid - adj_leader_px, 2)
        is_real = settings_manager.is_real_mode
        if is_real and not wallet_manager._balances.get("lighter_account_data_available"):
            self.current_decision = {
                "stance": "MONITORING",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": 0.0,
                "rationale": "REAL mode is waiting for a verified Lighter account equity snapshot; no order will use a simulated balance.",
                "rejection_reason": "REAL_ACCOUNT_SNAPSHOT_UNAVAILABLE",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
                "trading_mode": settings_manager.trading_mode,
                "paper_only": False,
            }
            return self.get_summary()
        try:
            reported_free_margin = float(wallet_manager._balances.get("lighter_free_margin_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            reported_free_margin = 0.0
        if is_real and reported_free_margin <= 0.0:
            self.current_decision = {
                "stance": "MONITORING",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": 0.0,
                "rationale": "REAL mode has no exchange-reported free margin; no order will use a simulated balance.",
                "rejection_reason": "REAL_FREE_MARGIN_UNAVAILABLE",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
                "trading_mode": settings_manager.trading_mode,
                "paper_only": False,
            }
            return self.get_summary()

        # Condition A: LONG Snipe
        # - Lighter Ask is cheaper than Basis-Adjusted Leader by >= MIN_LAG_TRIGGER
        # - AND a high-conviction major-venue leader is moving up at the configured velocity
        is_long_signal = (
            long_lag >= current_min_lag
            and l_ask > 0
            and leader_velocity >= self.min_entry_velocity
        )
        if is_long_signal:
            calc = self.calculate_trade_size(l_ask)
            profitability_limit = calculate_profitable_price_limit(
                side="LONG",
                target_price=adj_leader_px,
                target_exit_buffer_usd=self.target_exit_buffer_usd,
                minimum_expected_profit_usd=self.ladder_min_expected_profit_usd,
            )
            executable = calculate_executable_order(
                side="LONG",
                bids=lighter_state.get("bids", []),
                asks=lighter_state.get("asks", []),
                limit_price=profitability_limit,
                notional_cap_usd=calc["notional_usd"],
                max_levels=self.max_execution_book_levels,
                liquidity_participation=self.execution_liquidity_participation,
            )
            if not executable.meets_minimums:
                return self._reject_for_unexecutable_depth(
                    side="LONG",
                    entry_price=l_ask,
                    leader_name=leader_name,
                    calculated_size=calc,
                    executable=executable,
                    timestamp=now_str,
                )

            self.trade_counter += 1
            trade_size = executable.size_btc
            notional_usd = executable.notional_usd
            margin_usd = round(notional_usd / calc["leverage"], 2)
            entry_px = executable.vwap_price

            self.active_trade = {
                "id": self.trade_counter,
                "side": "LONG",
                "leader_name": leader_name,
                "size": trade_size,
                "size_btc": trade_size,
                "margin_allocated_usd": margin_usd,
                "leverage": calc["leverage"],
                "notional_usd": notional_usd,
                "requested_notional_usd": calc["notional_usd"],
                "visible_liquidity_btc": executable.visible_size_btc,
                "visible_liquidity_notional_usd": executable.visible_notional_usd,
                "execution_price_limit": executable.limit_price,
                "profitability_limit_price": executable.profitability_limit_price,
                "order_limit_notional_usd": executable.limit_notional_usd,
                "worst_case_notional_usd": executable.worst_case_notional_usd,
                "book_levels_used": executable.levels_used,
                "entry_px": entry_px,
                "entry_price": entry_px,
                "current_price": entry_px,
                "target_px": adj_leader_px,
                "target_price": adj_leader_px,
                "stop_loss_px": entry_px - self.stop_loss_drawdown,
                "stop_loss_price": entry_px - self.stop_loss_drawdown,
                "entry_leader_px": leader_px,
                "adj_leader_entry_px": adj_leader_px,
                "expected_lag": round(long_lag, 2),
                "floating_pnl_usd": 0.0,
                "entry_ts": now,
                "entry_time": now_str,
                "hold_seconds": 0.0,
                "exit_conditions": {
                    "target": f"Lighter bid >= ${adj_leader_px - self.target_exit_buffer_usd:,.1f}",
                    "invalidation": f"{leader_name} < ${leader_px - self.reversal_invalidation:,.1f}",
                    "hard_stop": f"Lighter bid <= ${entry_px - self.stop_loss_drawdown:,.1f}",
                    "timeout": f"{self.max_hold_seconds}s",
                },
                "mode": "REAL" if is_real else "SIMULATION",
                "paper_only": not is_real,
                "planned_size_btc": trade_size,
                "planned_entry_px": entry_px,
                "planned_notional_usd": notional_usd,
                "signal_ts": now,
                "execution_state": "ENTRY_SUBMITTED" if is_real else "OPEN",
                "order_status": "PENDING_SUBMISSION" if is_real else "SIMULATED",
                "latencies_ms": {},
                "tx_hash": None,
            }
            if is_real:
                self._begin_execution_telemetry(self.active_trade, lighter_state)
                self._fire_live_open(self.active_trade)

            self.current_decision = {
                "stance": "SIGNAL_DETECTED",
                "action": "SNIPE_LONG",
                "target_exchange": "Lighter.xyz (0% Fee DEX)",
                "elected_leader": leader_name,
                "signal_strength_usd": round(long_lag, 2),
                "rationale": f"⚡ Dynamic Leader [{leader_name}] at ${leader_px:,.1f} (velocity: ${leader_velocity:+.1f}) while Lighter Ask lags at ${l_ask:,.1f} (+${long_lag:.2f} dynamic lag, Mode: {settings_manager.trading_mode}). Sniping {trade_size:.5f} BTC LONG across {executable.levels_used} profitable book level(s) (VWAP ${entry_px:,.1f}, IOC limit ${executable.limit_price:,.1f}, ${notional_usd:,.2f} planned / ${executable.worst_case_notional_usd:,.2f} capped notional, {calc['leverage']:.0f}x, margin: ${margin_usd:.2f}).",
                "rejection_reason": None,
                "target_price": adj_leader_px,
                "stop_loss_price": entry_px - self.stop_loss_drawdown,
                "timestamp": now_str,
                "trading_mode": settings_manager.trading_mode,
                "paper_only": not is_real,
                "requested_notional_usd": calc["notional_usd"],
                "executable_notional_usd": notional_usd,
                "visible_liquidity_btc": executable.visible_size_btc,
                "visible_liquidity_notional_usd": executable.visible_notional_usd,
                "execution_price_limit": executable.limit_price,
                "profitability_limit_price": executable.profitability_limit_price,
                "order_limit_notional_usd": executable.limit_notional_usd,
                "worst_case_notional_usd": executable.worst_case_notional_usd,
                "book_levels_used": executable.levels_used,
                "entry_vwap_price": entry_px,
            }
            return self.get_summary()

        # Condition B: SHORT Snipe
        # - Lighter Bid is more expensive than Basis-Adjusted Leader by >= MIN_LAG_TRIGGER
        # - AND a high-conviction major-venue leader is moving down at the configured velocity
        is_short_signal = (
            short_lag >= current_min_lag
            and l_bid > 0
            and leader_velocity <= -self.min_entry_velocity
        )

        if is_short_signal:
            calc = self.calculate_trade_size(l_bid)
            profitability_limit = calculate_profitable_price_limit(
                side="SHORT",
                target_price=adj_leader_px,
                target_exit_buffer_usd=self.target_exit_buffer_usd,
                minimum_expected_profit_usd=self.ladder_min_expected_profit_usd,
            )
            executable = calculate_executable_order(
                side="SHORT",
                bids=lighter_state.get("bids", []),
                asks=lighter_state.get("asks", []),
                limit_price=profitability_limit,
                notional_cap_usd=calc["notional_usd"],
                max_levels=self.max_execution_book_levels,
                liquidity_participation=self.execution_liquidity_participation,
            )
            if not executable.meets_minimums:
                return self._reject_for_unexecutable_depth(
                    side="SHORT",
                    entry_price=l_bid,
                    leader_name=leader_name,
                    calculated_size=calc,
                    executable=executable,
                    timestamp=now_str,
                )

            self.trade_counter += 1
            trade_size = executable.size_btc
            notional_usd = executable.notional_usd
            margin_usd = round(notional_usd / calc["leverage"], 2)
            entry_px = executable.vwap_price

            self.active_trade = {
                "id": self.trade_counter,
                "side": "SHORT",
                "leader_name": leader_name,
                "size": trade_size,
                "size_btc": trade_size,
                "margin_allocated_usd": margin_usd,
                "leverage": calc["leverage"],
                "notional_usd": notional_usd,
                "requested_notional_usd": calc["notional_usd"],
                "visible_liquidity_btc": executable.visible_size_btc,
                "visible_liquidity_notional_usd": executable.visible_notional_usd,
                "execution_price_limit": executable.limit_price,
                "profitability_limit_price": executable.profitability_limit_price,
                "order_limit_notional_usd": executable.limit_notional_usd,
                "worst_case_notional_usd": executable.worst_case_notional_usd,
                "book_levels_used": executable.levels_used,
                "entry_px": entry_px,
                "entry_price": entry_px,
                "current_price": entry_px,
                "target_px": adj_leader_px,
                "target_price": adj_leader_px,
                "stop_loss_px": entry_px + self.stop_loss_drawdown,
                "stop_loss_price": entry_px + self.stop_loss_drawdown,
                "entry_leader_px": leader_px,
                "adj_leader_entry_px": adj_leader_px,
                "expected_lag": round(short_lag, 2),
                "floating_pnl_usd": 0.0,
                "entry_ts": now,
                "entry_time": now_str,
                "hold_seconds": 0.0,
                "exit_conditions": {
                    "target": f"Lighter ask <= ${adj_leader_px + self.target_exit_buffer_usd:,.1f}",
                    "invalidation": f"{leader_name} > ${leader_px + self.reversal_invalidation:,.1f}",
                    "hard_stop": f"Lighter ask >= ${entry_px + self.stop_loss_drawdown:,.1f}",
                    "timeout": f"{self.max_hold_seconds}s",
                },
                "mode": "REAL" if is_real else "SIMULATION",
                "paper_only": not is_real,
                "planned_size_btc": trade_size,
                "planned_entry_px": entry_px,
                "planned_notional_usd": notional_usd,
                "signal_ts": now,
                "execution_state": "ENTRY_SUBMITTED" if is_real else "OPEN",
                "order_status": "PENDING_SUBMISSION" if is_real else "SIMULATED",
                "latencies_ms": {},
                "tx_hash": None,
            }
            if is_real:
                self._begin_execution_telemetry(self.active_trade, lighter_state)
                self._fire_live_open(self.active_trade)

            self.current_decision = {
                "stance": "SIGNAL_DETECTED",
                "action": "SNIPE_SHORT",
                "target_exchange": "Lighter.xyz (0% Fee DEX)",
                "elected_leader": leader_name,
                "signal_strength_usd": round(short_lag, 2),
                "rationale": f"⚡ Dynamic Leader [{leader_name}] at ${leader_px:,.1f} (velocity: ${leader_velocity:+.1f}) while Lighter Bid lags at ${l_bid:,.1f} (-${short_lag:.2f} dynamic lag, Mode: {settings_manager.trading_mode}). Sniping {trade_size:.5f} BTC SHORT across {executable.levels_used} profitable book level(s) (VWAP ${entry_px:,.1f}, IOC limit ${executable.limit_price:,.1f}, ${notional_usd:,.2f} planned / ${executable.worst_case_notional_usd:,.2f} capped notional, {calc['leverage']:.0f}x, margin: ${margin_usd:.2f}).",
                "rejection_reason": None,
                "target_price": adj_leader_px,
                "stop_loss_price": entry_px + self.stop_loss_drawdown,
                "timestamp": now_str,
                "trading_mode": settings_manager.trading_mode,
                "paper_only": not is_real,
                "requested_notional_usd": calc["notional_usd"],
                "executable_notional_usd": notional_usd,
                "visible_liquidity_btc": executable.visible_size_btc,
                "visible_liquidity_notional_usd": executable.visible_notional_usd,
                "execution_price_limit": executable.limit_price,
                "profitability_limit_price": executable.profitability_limit_price,
                "order_limit_notional_usd": executable.limit_notional_usd,
                "worst_case_notional_usd": executable.worst_case_notional_usd,
                "book_levels_used": executable.levels_used,
                "entry_vwap_price": entry_px,
            }
            return self.get_summary()

        # Check why no trade was taken and report rationale
        max_lag = max(long_lag, short_lag)
        if max_lag < self.min_lag_trigger:
            self.current_decision = {
                "stance": "MONITORING",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": round(abs(l_mid - adj_leader_px), 2),
                "rationale": f"Lighter orderbook is aligned with {leader_name} (Dynamic lag: ${abs(l_mid - adj_leader_px):.2f} < ${self.min_lag_trigger:.1f} trigger). Leader velocity: ${leader_velocity:+.1f}.",
                "rejection_reason": "SPREAD_WITHIN_THRESHOLD",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
            }
        else:
            self.current_decision = {
                "stance": "MONITORING",
                "action": "NONE",
                "target_exchange": "Lighter.xyz",
                "elected_leader": leader_name,
                "signal_strength_usd": round(max_lag, 2),
                "rationale": f"Market steady. Dynamic Leader [{leader_name}] velocity is ${leader_velocity:+.1f} (need ±${self.min_entry_velocity:.1f}, Consensus: {consensus_status}). Standing down to avoid flat-market timeouts.",
                "rejection_reason": "INSUFFICIENT_VELOCITY",
                "target_price": None,
                "stop_loss_price": None,
                "timestamp": now_str,
            }
        return self.get_summary()

    def get_performance(self) -> Dict[str, Any]:
        mode = settings_manager.trading_mode
        simulation_net_pnl = sum(
            float(trade.get("net_pnl", 0.0))
            for trade in self.closed_trades
            if str(trade.get("mode", "")).upper() != "REAL"
        )
        performance = build_performance(
            closed_trades=self.closed_trades,
            active_trade=self.active_trade,
            mode=mode,
            leverage=settings_manager.leverage,
            margin_fraction=settings_manager.trade_margin_fraction,
            simulation_balance=round(self.base_balance_usd + simulation_net_pnl, 2),
            real_account=wallet_manager._balances,
        )
        performance["trading_enabled"] = settings_manager.trading_enabled
        return performance

    def get_summary(self) -> Dict[str, Any]:
        return {
            "decision": self.current_decision,
            "active_position": self.active_trade,
            "closed_trades": list(self.closed_trades),
            "execution_attempts": self.get_execution_attempts(),
            "performance": self.get_performance(),
            "trading_mode": settings_manager.trading_mode,
            "is_real_mode": settings_manager.is_real_mode,
            "trading_enabled": settings_manager.trading_enabled,
        }
