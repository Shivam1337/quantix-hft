"""Fill-confirmed real-mode execution lifecycle for ``SniperEngine``."""
import asyncio
import time
from datetime import datetime
from typing import Any, Dict

from app.core.lighter_order_reconciliation import LighterOrderOutcome
RECONCILIATION_WINDOW_SECONDS = 2.0
RECONCILIATION_RETRY_SECONDS = 0.25
BTC_PRECISION_EPSILON = 0.000005
class LiveExecutionMixin:
    """Keeps live order submission separate from strategy and PnL decision logic."""
    def _init_live_execution(self) -> None:
        self._live_execution_tasks: set[asyncio.Task] = set()
    def _schedule_live_task(self, coroutine: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coroutine)
        except RuntimeError:
            return
        self._live_execution_tasks.add(task)
        task.add_done_callback(self._live_execution_tasks.discard)
    @staticmethod
    def _record_latency(trade: Dict[str, Any], name: str, start: Any, end: Any) -> None:
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            trade.setdefault("latencies_ms", {})[name] = round(max(0.0, end - start) * 1000, 2)
    def _fire_live_open(self, trade: Dict[str, Any]) -> None:
        trade["execution_state"] = "ENTRY_SUBMITTED"
        self._schedule_live_task(self._execute_live_open(trade))
    async def _execute_live_open(self, trade: Dict[str, Any]) -> None:
        from app.core.lighter_client import lighter_client
        if self.active_trade is not trade:
            return
        submitted_at = time.time()
        trade["entry_submit_ts"] = submitted_at
        self._record_latency(trade, "signal_to_submit", trade.get("signal_ts"), submitted_at)
        success, tx_hash, error = await lighter_client.open_snipe_order(
            side=trade["side"],
            size_btc=trade["size_btc"],
            limit_price=trade.get("execution_price_limit", trade["entry_px"]),
            trade_id=trade["id"],
        )
        acknowledged_at = time.time()
        if not success:
            trade.update({"order_error": error, "order_status": "FAILED", "execution_state": "ENTRY_FAILED"})
            if self.active_trade is trade:
                self.active_trade = None
                self.last_close_ts = acknowledged_at
            return
        trade.update({
            "tx_hash": tx_hash,
            "order_status": "SUBMITTED",
            "execution_state": "ENTRY_RECONCILING",
            "entry_ack_ts": acknowledged_at,
        })
        self._record_latency(trade, "submit_to_ack", submitted_at, acknowledged_at)
        await self._reconcile_entry(trade, lighter_client)
    async def _reconcile_entry(self, trade: Dict[str, Any], client: Any) -> None:
        while self.active_trade is trade:
            try:
                outcome = await client.wait_for_order_outcome(
                    client_order_index=trade["id"],
                    submitted_at=trade.get("entry_submit_ts"),
                    timeout_seconds=RECONCILIATION_WINDOW_SECONDS,
                )
            except Exception as exc:
                trade["reconciliation_error"] = str(exc)
                outcome = None
            if outcome is None:
                trade["execution_state"] = "ENTRY_RECONCILIATION_PENDING"
                await asyncio.sleep(RECONCILIATION_RETRY_SECONDS)
                continue
            if not outcome.has_fill:
                trade.update({
                    "entry_terminal_status": outcome.status,
                    "order_status": outcome.status.upper(),
                    "execution_state": "ENTRY_NOT_FILLED",
                    "entry_fill_status": "NONE",
                })
                if self.active_trade is trade:
                    self.active_trade = None
                    self.last_close_ts = time.time()
                return
            self._apply_entry_fill(trade, outcome)
            return
    def _apply_entry_fill(self, trade: Dict[str, Any], outcome: LighterOrderOutcome) -> None:
        observed_at = time.time()
        planned_size = float(trade.get("planned_size_btc", trade["size_btc"]))
        fill_price = outcome.average_fill_price or float(trade["entry_px"])
        fill_time = outcome.exchange_timestamp or observed_at
        if fill_time < trade.get("entry_submit_ts", fill_time) - 15.0 or fill_time > observed_at + 15.0:
            fill_time = observed_at
        actual_notional = outcome.filled_quote_usd or outcome.filled_size_btc * fill_price
        trade.update({
            "entry_filled_size_btc": outcome.filled_size_btc,
            "filled_size_btc": outcome.filled_size_btc,
            "size": outcome.filled_size_btc,
            "size_btc": outcome.filled_size_btc,
            "remaining_size_btc": outcome.filled_size_btc,
            "entry_px": fill_price,
            "entry_price": fill_price,
            "entry_avg_fill_price": fill_price,
            "current_price": fill_price,
            "notional_usd": round(actual_notional, 2),
            "margin_allocated_usd": round(actual_notional / trade["leverage"], 2),
            "entry_ts": fill_time,
            "entry_time": datetime.fromtimestamp(fill_time).strftime("%H:%M:%S"),
            "entry_fill_observed_ts": observed_at,
            "entry_exchange_timestamp": outcome.exchange_timestamp,
            "entry_terminal_status": outcome.status,
            "order_status": outcome.status.upper(),
            "execution_state": "OPEN",
            "entry_fill_status": "PARTIAL" if outcome.filled_size_btc + BTC_PRECISION_EPSILON < planned_size else "FILLED",
        })
        trade["stop_loss_px"] = fill_price - self.stop_loss_drawdown if trade["side"] == "LONG" else fill_price + self.stop_loss_drawdown
        trade["stop_loss_price"] = trade["stop_loss_px"]
        trade["exit_conditions"]["hard_stop"] = (
            f"Lighter {'bid' if trade['side'] == 'LONG' else 'ask'} "
            f"{'<=' if trade['side'] == 'LONG' else '>='} ${trade['stop_loss_px']:,.1f}"
        )
        self._record_latency(trade, "ack_to_fill", trade.get("entry_ack_ts"), observed_at)
        self._record_latency(trade, "signal_to_fill", trade.get("signal_ts"), observed_at)
    def _fire_live_close(self, trade: Dict[str, Any], exit_price: float, exit_reason: str) -> None:
        trade.update({
            "execution_state": "EXIT_SUBMITTED",
            "exit_signal_ts": time.time(),
            "exit_requested_px": exit_price,
            "exit_reason": exit_reason,
        })
        self._schedule_live_task(self._execute_live_close(trade))
    async def _execute_live_close(self, trade: Dict[str, Any]) -> None:
        from app.core.lighter_client import lighter_client
        if self.active_trade is not trade:
            return
        submitted_at = time.time()
        trade["exit_submit_ts"] = submitted_at
        self._record_latency(trade, "fill_to_exit_submit", trade.get("entry_fill_observed_ts"), submitted_at)
        success, tx_hash, error = await lighter_client.close_snipe_order(
            side=trade["side"],
            size_btc=trade["size_btc"],
            limit_price=trade["exit_requested_px"],
            trade_id=trade["id"],
        )
        acknowledged_at = time.time()
        if not success:
            trade.update({"exit_order_error": error, "execution_state": "OPEN"})
            return
        trade.update({
            "exit_tx_hash": tx_hash,
            "exit_ack_ts": acknowledged_at,
            "execution_state": "EXIT_RECONCILING",
        })
        self._record_latency(trade, "exit_submit_to_ack", submitted_at, acknowledged_at)
        await self._reconcile_exit(trade, lighter_client)
    async def _reconcile_exit(self, trade: Dict[str, Any], client: Any) -> None:
        while self.active_trade is trade:
            try:
                outcome = await client.wait_for_order_outcome(
                    client_order_index=trade["id"] + 10_000,
                    submitted_at=trade.get("exit_submit_ts"),
                    timeout_seconds=RECONCILIATION_WINDOW_SECONDS,
                )
            except Exception as exc:
                trade["exit_reconciliation_error"] = str(exc)
                outcome = None
            if outcome is None:
                trade["execution_state"] = "EXIT_RECONCILIATION_PENDING"
                await asyncio.sleep(RECONCILIATION_RETRY_SECONDS)
                continue
            self._apply_exit_fill(trade, outcome)
            return
    def _apply_exit_fill(self, trade: Dict[str, Any], outcome: LighterOrderOutcome) -> None:
        observed_at = time.time()
        requested = float(trade["size_btc"])
        filled_size = min(requested, outcome.filled_size_btc)
        fill_price = outcome.average_fill_price
        if filled_size <= 0 or fill_price is None:
            trade.update({"exit_terminal_status": outcome.status, "execution_state": "OPEN"})
            return
        pnl = (fill_price - trade["entry_px"]) * filled_size if trade["side"] == "LONG" else (trade["entry_px"] - fill_price) * filled_size
        fills = trade.setdefault("exit_fills", [])
        fills.append({"size_btc": filled_size, "price": fill_price, "status": outcome.status})
        total_closed = sum(float(item["size_btc"]) for item in fills)
        entry_size = float(trade["entry_filled_size_btc"])
        remaining = max(0.0, entry_size - total_closed)
        trade["realized_pnl_usd"] = round(float(trade.get("realized_pnl_usd", 0.0)) + pnl, 6)
        trade.update({
            "filled_exit_size_btc": total_closed,
            "remaining_size_btc": remaining,
            "exit_terminal_status": outcome.status,
            "exit_fill_observed_ts": observed_at,
            "exit_exchange_timestamp": outcome.exchange_timestamp,
        })
        self._record_latency(trade, "exit_ack_to_fill", trade.get("exit_ack_ts"), observed_at)
        self._record_latency(trade, "exit_signal_to_fill", trade.get("exit_signal_ts"), observed_at)
        if remaining > BTC_PRECISION_EPSILON:
            trade.update({
                "size": remaining,
                "size_btc": remaining,
                "notional_usd": round(remaining * trade["entry_px"], 2),
                "margin_allocated_usd": round(remaining * trade["entry_px"] / trade["leverage"], 2),
                "execution_state": "OPEN",
                "exit_fill_status": "PARTIAL",
            })
            return
        self._record_live_close(trade, observed_at)
    def _record_live_close(self, trade: Dict[str, Any], closed_at: float) -> None:
        fills = trade.get("exit_fills", [])
        total_size = sum(float(item["size_btc"]) for item in fills)
        exit_price = sum(float(item["size_btc"]) * float(item["price"]) for item in fills) / total_size
        net_pnl = float(trade.get("realized_pnl_usd", 0.0))
        closed = {
            "id": trade["id"], "time": datetime.fromtimestamp(closed_at).strftime("%H:%M:%S"),
            "side": trade["side"], "leader": trade.get("leader_name", "Leader"),
            "size": total_size, "size_btc": total_size, "entry_px": trade["entry_px"],
            "entry_price": trade["entry_px"], "exit_px": exit_price, "exit_price": exit_price,
            "gross_pnl": round(net_pnl, 3), "fees_paid": 0.0, "net_pnl": round(net_pnl, 3),
            "hold_sec": round(max(0.0, closed_at - trade["entry_ts"]), 1),
            "reason": trade.get("exit_reason", "EXIT_FILLED"), "is_win": net_pnl > 0,
            "margin_allocated_usd": trade.get("margin_allocated_usd", 0.0),
            "leverage": trade["leverage"], "notional_usd": round(total_size * trade["entry_px"], 2),
            "mode": "REAL", "paper_only": False, "tx_hash": trade.get("tx_hash"),
            "exit_tx_hash": trade.get("exit_tx_hash"), "order_status": "CLOSED",
            "execution_state": "EXIT_FILLED", "entry_fill_status": trade.get("entry_fill_status"),
            "exit_fill_status": "FILLED", "latencies_ms": dict(trade.get("latencies_ms", {})),
            "cost_model": "Confirmed Lighter IOC fills; no fee model is applied.",
        }
        if self.active_trade is trade:
            self.closed_trades.appendleft(closed)
            self.last_close_ts = closed_at
            self.active_trade = None
    def _live_execution_pending_summary(self, leader_name: str, timestamp: str) -> Dict[str, Any]:
        trade = self.active_trade
        self.current_decision = {
            "stance": "ENTRY_PENDING" if str(trade.get("execution_state", "")).startswith("ENTRY") else "EXIT_PENDING",
            "action": "WAIT_FOR_FILL",
            "target_exchange": "Lighter.xyz",
            "elected_leader": trade.get("leader_name", leader_name),
            "signal_strength_usd": 0.0,
            "rationale": f"Waiting for Lighter-confirmed {trade.get('execution_state', 'order')} before changing position state.",
            "rejection_reason": None,
            "target_price": trade.get("target_px"),
            "stop_loss_price": trade.get("stop_loss_px"),
            "timestamp": timestamp,
            "trading_mode": "REAL",
            "paper_only": False,
        }
        return self.get_summary()
