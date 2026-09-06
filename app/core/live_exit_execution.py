"""Reduce-only exit lifecycle for fill-confirmed Lighter positions."""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict

from app.core.lighter_order_reconciliation import LighterOrderOutcome


logger = logging.getLogger(__name__)

RECONCILIATION_WINDOW_SECONDS = 2.0
RECONCILIATION_RETRY_SECONDS = 0.25
BTC_PRECISION_EPSILON = 0.000005


class LiveExitExecutionMixin:
    """Owns exits so entry execution remains compact and independently testable."""

    def _fire_live_close(self, trade: Dict[str, Any], exit_price: float, exit_reason: str) -> None:
        self._capture_dual_exit_signal(trade, exit_price, exit_reason)
        buffer = float(getattr(self, "execution_slippage_buffer_usd", 3.0))
        if trade.get("side") == "LONG":
            exit_limit = round(max(0.1, exit_price - buffer), 1)
        else:
            exit_limit = round(exit_price + buffer, 1)

        trade.update({
            "execution_state": "EXIT_SUBMITTED",
            "exit_signal_ts": time.time(),
            "exit_requested_px": exit_price,
            "exit_limit_px": exit_limit,
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
        self._capture_order_submission(trade, "EXIT", submitted_at)
        exit_limit = trade.get("exit_limit_px", trade["exit_requested_px"])
        success, tx_hash, error = await lighter_client.close_snipe_order(
            side=trade["side"], size_btc=trade["size_btc"],
            limit_price=exit_limit, trade_id=trade["id"],
        )
        acknowledged_at = time.time()
        if not success:
            trade.update({"exit_order_error": error, "execution_state": "OPEN"})
            self._record_dual_exit_failure(trade, error)
            self._record_execution_terminal(
                trade, phase="EXIT", result="EXIT_SUBMISSION_FAILED", observed_at=acknowledged_at, error=error,
            )
            return
        trade.update({"exit_tx_hash": tx_hash, "exit_ack_ts": acknowledged_at, "execution_state": "EXIT_RECONCILING"})
        self._record_latency(trade, "exit_submit_to_ack", submitted_at, acknowledged_at)
        self._record_order_acknowledgement(trade, "EXIT", acknowledged_at, tx_hash)
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
            self._record_execution_terminal(
                trade, phase="EXIT", result="EXIT_NOT_FILLED", observed_at=observed_at, outcome=outcome,
            )
            return
        pnl = (fill_price - trade["entry_px"]) * filled_size if trade["side"] == "LONG" else (trade["entry_px"] - fill_price) * filled_size
        fills = trade.setdefault("exit_fills", [])
        fills.append({"size_btc": filled_size, "price": fill_price, "status": outcome.status})
        total_closed = sum(float(item["size_btc"]) for item in fills)
        entry_size = float(trade["entry_filled_size_btc"])
        remaining = max(0.0, entry_size - total_closed)
        trade["realized_pnl_usd"] = round(float(trade.get("realized_pnl_usd", 0.0)) + pnl, 6)
        trade.update({
            "filled_exit_size_btc": total_closed, "remaining_size_btc": remaining,
            "exit_terminal_status": outcome.status, "exit_fill_observed_ts": observed_at,
            "exit_exchange_timestamp": outcome.exchange_timestamp,
        })
        self._record_latency(trade, "exit_ack_to_fill", trade.get("exit_ack_ts"), observed_at)
        self._record_latency(trade, "exit_signal_to_fill", trade.get("exit_signal_ts"), observed_at)
        if remaining > BTC_PRECISION_EPSILON:
            trade.update({
                "size": remaining, "size_btc": remaining,
                "notional_usd": round(remaining * trade["entry_px"], 2),
                "margin_allocated_usd": round(remaining * trade["entry_px"] / trade["leverage"], 2),
                "execution_state": "OPEN", "exit_fill_status": "PARTIAL",
            })
            self._record_execution_terminal(
                trade, phase="EXIT", result="EXIT_PARTIAL", observed_at=observed_at, outcome=outcome,
            )
            return
        self._record_execution_terminal(
            trade, phase="EXIT", result="EXIT_FILLED", observed_at=observed_at, outcome=outcome,
        )
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
        self._finalize_dual_comparison(trade, closed)
        if self.active_trade is trade:
            self.closed_trades.appendleft(closed)
            self.last_close_ts = closed_at
            self.active_trade = None
            logger.info(
                "Confirmed Lighter position closed! Trade #%s, side=%s, exit_px=%s, net_pnl=$%s",
                trade.get("id"), trade.get("side"), round(exit_price, 2), round(net_pnl, 4),
            )

    def _live_execution_pending_summary(self, leader_name: str, timestamp: str) -> Dict[str, Any]:
        trade = self.active_trade
        self.current_decision = {
            "stance": "ENTRY_PENDING" if str(trade.get("execution_state", "")).startswith("ENTRY") else "EXIT_PENDING",
            "action": "WAIT_FOR_FILL", "target_exchange": "Lighter.xyz",
            "elected_leader": trade.get("leader_name", leader_name), "signal_strength_usd": 0.0,
            "rationale": f"Waiting for Lighter-confirmed {trade.get('execution_state', 'order')} before changing position state.",
            "rejection_reason": None, "target_price": trade.get("target_px"),
            "stop_loss_price": trade.get("stop_loss_px"), "timestamp": timestamp,
            "trading_mode": "REAL", "paper_only": False,
        }
        return self.get_summary()
