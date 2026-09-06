"""Fill-confirmed real-mode execution lifecycle for ``SniperEngine``."""
import asyncio
import time
from datetime import datetime
from typing import Any, Dict

from app.core.execution.order_safety import LiveOrderSafetyMixin
from app.core.execution.submission import coerce_submission_receipt
from app.core.live_exit_execution import LiveExitExecutionMixin
from app.core.lighter_order_reconciliation import LighterOrderOutcome


RECONCILIATION_WINDOW_SECONDS = 2.0
RECONCILIATION_RETRY_SECONDS = 0.25
RECONCILIATION_MAX_ATTEMPTS = 3
BTC_PRECISION_EPSILON = 0.000005


class LiveExecutionMixin(LiveOrderSafetyMixin, LiveExitExecutionMixin):
    """Keeps live order submission separate from strategy and PnL decision logic."""
    def _init_live_execution(self) -> None:
        self._live_execution_tasks: set[asyncio.Task] = set()
        self._init_order_safety()

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
        _, safety_error = self._prepare_live_entry_at_arrival(trade)
        if safety_error:
            self._fail_live_entry(trade, safety_error, result="ENTRY_SAFETY_REJECTED")
            return
        submitted_at = time.time()
        trade["entry_submit_ts"] = submitted_at
        self._record_latency(trade, "signal_to_submit", trade.get("signal_ts"), submitted_at)
        self._capture_order_submission(trade, "ENTRY", submitted_at)
        client_order_index, journal_error = await self._reserve_order_intent(trade, "ENTRY", submitted_at)
        if journal_error or client_order_index is None:
            self._fail_live_entry(trade, journal_error or "ORDER_JOURNAL_INTENT_FAILED", result="ENTRY_SUBMISSION_FAILED")
            return
        receipt = coerce_submission_receipt(await lighter_client.open_snipe_order(
            side=trade["side"],
            size_btc=trade["size_btc"],
            limit_price=trade.get("execution_price_limit", trade["entry_px"]),
            trade_id=trade["id"],
            client_order_index=client_order_index,
        ))
        acknowledged_at = time.time()
        if not receipt.success:
            if receipt.uncertain:
                await self._quarantine_unknown_order(trade, "ENTRY", receipt.error or "Uncertain Lighter submission outcome.")
                self._record_dual_entry_failure(trade, status="LIVE_ENTRY_UNKNOWN", error=receipt.error)
                self._record_execution_terminal(trade, phase="ENTRY", result="ENTRY_UNKNOWN", observed_at=acknowledged_at, error=receipt.error)
                return
            journal_error = await self._record_order_terminal(
                client_order_index, terminal_status="SUBMISSION_FAILED", error=receipt.error,
            )
            if journal_error:
                await self._quarantine_unknown_order(trade, "ENTRY", journal_error)
                self._record_dual_entry_failure(trade, status="LIVE_ENTRY_UNKNOWN", error=journal_error)
                self._record_execution_terminal(trade, phase="ENTRY", result="ENTRY_UNKNOWN", observed_at=acknowledged_at, error=journal_error)
                return
            self._fail_live_entry(trade, receipt.error or "Lighter submission failed.", result="ENTRY_SUBMISSION_FAILED")
            return
        journal_error = await self._acknowledge_order_intent(client_order_index, receipt)
        if journal_error:
            await self._quarantine_unknown_order(trade, "ENTRY", journal_error)
            self._record_dual_entry_failure(trade, status="LIVE_ENTRY_UNKNOWN", error=journal_error)
            self._record_execution_terminal(trade, phase="ENTRY", result="ENTRY_UNKNOWN", observed_at=acknowledged_at, error=journal_error)
            return
        trade.update({
            "tx_hash": receipt.tx_hash,
            "order_status": "SUBMITTED",
            "execution_state": "ENTRY_RECONCILING",
            "entry_ack_ts": acknowledged_at,
        })
        self._record_latency(trade, "submit_to_ack", submitted_at, acknowledged_at)
        self._record_order_acknowledgement(trade, "ENTRY", acknowledged_at, receipt.tx_hash, receipt)
        self._record_arrival_ack(trade, "ENTRY")
        await self._reconcile_entry(trade, lighter_client)

    async def _reconcile_entry(self, trade: Dict[str, Any], client: Any) -> None:
        for attempt in range(RECONCILIATION_MAX_ATTEMPTS):
            if self.active_trade is not trade:
                return
            try:
                outcome = await client.wait_for_order_outcome(
                    client_order_index=trade.get("entry_client_order_index", trade["id"]),
                    submitted_at=trade.get("entry_submit_ts"),
                    timeout_seconds=RECONCILIATION_WINDOW_SECONDS,
                )
            except Exception as exc:
                trade["reconciliation_error"] = str(exc)
                outcome = None
            if outcome is None:
                trade["execution_state"] = "ENTRY_RECONCILIATION_PENDING"
                if attempt + 1 >= RECONCILIATION_MAX_ATTEMPTS:
                    error = trade.get("reconciliation_error", "Lighter entry outcome did not arrive within the bounded window.")
                    await self._quarantine_unknown_order(trade, "ENTRY", str(error))
                    self._record_dual_entry_failure(trade, status="LIVE_ENTRY_UNKNOWN", error=str(error))
                    self._record_execution_terminal(trade, phase="ENTRY", result="ENTRY_UNKNOWN", error=str(error))
                    return
                await asyncio.sleep(RECONCILIATION_RETRY_SECONDS)
                continue
            observed_at = time.time()
            client_order_index = int(trade.get("entry_client_order_index", trade["id"]))
            journal_error = (
                await self._mark_entry_position_open(client_order_index, outcome.status)
                if outcome.has_fill
                else await self._record_order_terminal(client_order_index, terminal_status=outcome.status)
            )
            if journal_error:
                await self._quarantine_unknown_order(trade, "ENTRY", journal_error)
                self._record_dual_entry_failure(trade, status="LIVE_ENTRY_UNKNOWN", error=journal_error)
                self._record_execution_terminal(trade, phase="ENTRY", result="ENTRY_UNKNOWN", observed_at=observed_at, error=journal_error)
                return
            self._record_entry_terminal_safety(outcome.status)
            if not outcome.has_fill:
                trade.update({
                    "entry_terminal_status": outcome.status,
                    "order_status": outcome.status.upper(),
                    "execution_state": "ENTRY_NOT_FILLED",
                    "entry_fill_status": "NONE",
                    "entry_terminal_observed_ts": observed_at,
                })
                self._record_dual_entry_failure(
                    trade, status="LIVE_ENTRY_NOT_FILLED", error=outcome.status,
                )
                self._record_execution_terminal(
                    trade, phase="ENTRY", result="ENTRY_NOT_FILLED", observed_at=observed_at, outcome=outcome,
                )
                if self.active_trade is trade:
                    self.active_trade = None
                    self.last_close_ts = time.time()
                return
            self._apply_entry_fill(trade, outcome)
            return

    def _fail_live_entry(self, trade: Dict[str, Any], error: str, *, result: str) -> None:
        observed_at = time.time()
        trade.update({"order_error": error, "order_status": "FAILED", "execution_state": "ENTRY_FAILED"})
        self._record_dual_entry_failure(trade, status="LIVE_ENTRY_SUBMISSION_FAILED", error=error)
        self._record_execution_terminal(trade, phase="ENTRY", result=result, observed_at=observed_at, error=error)
        if self.active_trade is trade:
            self.active_trade = None
            self.last_close_ts = observed_at

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
        self._record_dual_entry_fill(trade)
        self._record_execution_terminal(
            trade,
            phase="ENTRY",
            result="ENTRY_PARTIAL" if trade["entry_fill_status"] == "PARTIAL" else "ENTRY_FILLED",
            observed_at=observed_at,
            outcome=outcome,
        )
