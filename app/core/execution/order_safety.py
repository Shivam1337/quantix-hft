"""Durable order intent, arrival economics, and unknown-state containment."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Mapping, Optional, Tuple

from app.config import (
    LIGHTER_MAX_EXECUTION_BOOK_AGE_MS,
    LIGHTER_MAXIMUM_ARRIVAL_MS,
    LIGHTER_MINIMUM_ARRIVAL_MS,
)
from app.core.execution.economics import ArrivalTimeOrder, calculate_arrival_time_executable_order
from app.core.execution.latency import ExecutionLatencyGuard
from app.core.execution.order_journal import JournalOrder, OrderJournal
from app.core.execution.telemetry import capture_lighter_book
from app.core.execution.submission import LighterSubmissionReceipt


class LiveOrderSafetyMixin:
    """Shared safety controls for entry and reduce-only IOC lifecycle code."""

    def _init_order_safety(self) -> None:
        self.execution_latency_guard = ExecutionLatencyGuard(
            minimum_arrival_ms=LIGHTER_MINIMUM_ARRIVAL_MS,
            maximum_arrival_ms=LIGHTER_MAXIMUM_ARRIVAL_MS,
            maximum_book_age_ms=LIGHTER_MAX_EXECUTION_BOOK_AGE_MS,
        )
        # Direct engine users get deterministic in-process IDs.  StateManager
        # replaces this with the durable WAL journal before feeds are started.
        self._order_journal = OrderJournal()
        self._live_entry_block_reason: Optional[str] = None
        self._journal_recovery_orders: list[JournalOrder] = []

    def configure_order_journal(self, journal: OrderJournal) -> None:
        self._order_journal = journal

    @property
    def live_entry_block_reason(self) -> Optional[str]:
        return self._live_entry_block_reason

    async def recover_unresolved_live_orders(self) -> list[JournalOrder]:
        unresolved = await self._journal_call(self._order_journal.unresolved_orders)
        self._journal_recovery_orders = unresolved
        if unresolved:
            self._block_new_live_entries(
                f"ORDER_JOURNAL_RECOVERY_REQUIRED ({len(unresolved)} unresolved Lighter order(s))"
            )
        return unresolved

    def _block_new_live_entries(self, reason: str) -> None:
        self._live_entry_block_reason = reason

    def _prepare_live_entry_at_arrival(self, trade: Dict[str, Any]) -> Tuple[Optional[ArrivalTimeOrder], Optional[str]]:
        state, source_error = self._get_execution_book_state()
        if state is None:
            # Unit-level users of SniperEngine do not always attach a market
            # state provider.  The production StateManager always does; only
            # an actual provider failure is a live safety rejection here.
            return (None, source_error) if source_error else (None, None)
        book = capture_lighter_book(state)
        quote_age = book.get("book_age_ms")
        side = str(trade.get("side", ""))
        configured_buffer = float(getattr(self, "execution_slippage_buffer_usd", 0.0))
        latency_buffer = max(configured_buffer, self.execution_latency_guard.adverse_quote_buffer_usd)
        plan = calculate_arrival_time_executable_order(
            side=side,
            bids=state.get("bids", []),
            asks=state.get("asks", []),
            target_price=float(trade.get("target_px", 0.0)),
            target_exit_buffer_usd=float(getattr(self, "target_exit_buffer_usd", 0.0)),
            minimum_net_profit_usd=float(trade.get("minimum_net_profit_usd", getattr(self, "ladder_min_expected_profit_usd", 0.0))),
            estimated_cost_usd=float(trade.get("estimated_cost_usd", 0.0)),
            latency_buffer_usd=latency_buffer,
            notional_cap_usd=float(trade.get("requested_notional_usd", trade.get("notional_usd", 0.0))),
            max_levels=int(getattr(self, "max_execution_book_levels", 1)),
            liquidity_participation=float(getattr(self, "execution_liquidity_participation", 1.0)),
            slippage_buffer_usd=latency_buffer,
        )
        quote_key = "best_ask" if side.upper() == "LONG" else "best_bid"
        quote = _number(book.get(quote_key))
        signal_key = f"{side}:{trade.get('leader_name')}:{round(float(trade.get('target_px', 0.0)), 1)}:{round(quote, 1)}"
        self.execution_latency_guard.note_signal(signal_key)
        decision = self.execution_latency_guard.check_entry(
            signal_key=signal_key,
            quote_age_ms=quote_age,
            projected_net_profit_usd=plan.projected_net_profit_usd,
            minimum_net_profit_usd=plan.minimum_net_profit_usd,
        )
        if not plan.executable.meets_minimums:
            return None, "INSUFFICIENT_EXECUTABLE_LIQUIDITY"
        if not plan.meets_economics:
            return None, "INSUFFICIENT_ARRIVAL_ECONOMICS"
        if not decision.allowed:
            return None, decision.reason
        executable = plan.executable
        stop_loss = (
            executable.vwap_price - float(getattr(self, "stop_loss_drawdown", 0.0))
            if side.upper() == "LONG"
            else executable.vwap_price + float(getattr(self, "stop_loss_drawdown", 0.0))
        )
        trade.update({
            "size": executable.size_btc,
            "size_btc": executable.size_btc,
            "notional_usd": executable.notional_usd,
            "margin_allocated_usd": round(executable.notional_usd / float(trade["leverage"]), 2),
            "entry_px": executable.vwap_price,
            "entry_price": executable.vwap_price,
            "current_price": executable.vwap_price,
            "stop_loss_px": stop_loss,
            "stop_loss_price": stop_loss,
            "execution_price_limit": executable.limit_price,
            "ladder_price_limit": executable.ladder_limit_price,
            "profitability_limit_price": executable.profitability_limit_price,
            "order_limit_notional_usd": executable.limit_notional_usd,
            "worst_case_notional_usd": executable.worst_case_notional_usd,
            "book_levels_used": executable.levels_used,
            "arrival_target_exit_price": plan.target_exit_price,
            "arrival_required_price_edge_usd": plan.required_price_edge_usd,
            "arrival_latency_buffer_usd": plan.latency_buffer_usd,
            "arrival_projected_net_profit_usd": plan.projected_net_profit_usd,
            "arrival_budget_ms": decision.arrival_budget_ms,
            "entry_arrival_quote": quote,
        })
        if isinstance(trade.get("exit_conditions"), dict):
            trade["exit_conditions"]["hard_stop"] = (
                f"Lighter {'bid' if side.upper() == 'LONG' else 'ask'} "
                f"{'<=' if side.upper() == 'LONG' else '>='} ${stop_loss:,.1f}"
            )
        return plan, None

    async def _reserve_order_intent(self, trade: Dict[str, Any], phase: str, submitted_at: float) -> Tuple[Optional[int], Optional[str]]:
        limit = trade.get("execution_price_limit") if phase == "ENTRY" else trade.get("exit_limit_px")
        try:
            index = await self._journal_call(
                self._order_journal.reserve_intent,
                trade_id=int(trade["id"]),
                phase=phase,
                side=str(trade["side"]),
                size_btc=float(trade["size_btc"]),
                limit_price=float(limit),
                submitted_at=submitted_at,
            )
        except Exception as exc:
            return None, f"ORDER_JOURNAL_INTENT_FAILED: {exc}"
        trade[f"{phase.lower()}_client_order_index"] = index
        return index, None

    async def _acknowledge_order_intent(self, client_order_index: int, receipt: LighterSubmissionReceipt) -> Optional[str]:
        try:
            await self._journal_call(
                self._order_journal.acknowledge,
                client_order_index,
                tx_hash=receipt.tx_hash,
                response_code=receipt.response_code,
                response_message=receipt.response_message,
            )
            return None
        except Exception as exc:
            return f"ORDER_JOURNAL_ACK_FAILED: {exc}"

    async def _record_order_terminal(
        self, client_order_index: int, *, terminal_status: str, error: Optional[str] = None
    ) -> Optional[str]:
        try:
            await self._journal_call(
                self._order_journal.record_terminal,
                client_order_index,
                terminal_status=terminal_status,
                error=error,
            )
            return None
        except Exception as exc:
            return f"ORDER_JOURNAL_TERMINAL_FAILED: {exc}"

    async def _mark_entry_position_open(self, client_order_index: int, terminal_status: str) -> Optional[str]:
        try:
            await self._journal_call(
                self._order_journal.mark_position_open,
                client_order_index,
                terminal_status=terminal_status,
            )
            return None
        except Exception as exc:
            return f"ORDER_JOURNAL_POSITION_FAILED: {exc}"

    async def _quarantine_unknown_order(self, trade: Dict[str, Any], phase: str, error: str) -> None:
        client_order_index = trade.get(f"{phase.lower()}_client_order_index")
        if client_order_index is not None:
            try:
                await self._journal_call(self._order_journal.mark_unknown, int(client_order_index), error=error)
            except Exception:
                error = f"{error}; ORDER_JOURNAL_UNKNOWN_WRITE_FAILED"
        trade["execution_state"] = f"{phase}_UNKNOWN"
        trade[f"{phase.lower()}_reconciliation_error"] = error
        self._block_new_live_entries(f"LIVE_ORDER_STATE_UNKNOWN: {phase} order requires Lighter reconciliation")

    def _record_arrival_ack(self, trade: Dict[str, Any], phase: str) -> None:
        order = trade.get("execution_telemetry", {}).get("orders", {}).get(phase.lower(), {})
        submitted_book = order.get("lighter_book") if isinstance(order, Mapping) else None
        acknowledgement_book = order.get("acknowledgement_book") if isinstance(order, Mapping) else None
        latency = trade.get("latencies_ms", {}).get(f"{phase.lower()}_submit_to_ack")
        side = trade.get("side") if phase == "ENTRY" else _opposite_side(trade.get("side"))
        self.execution_latency_guard.record_acknowledgement(
            side=str(side or ""),
            submit_quote=_quote_for_side(submitted_book, side),
            acknowledged_quote=_quote_for_side(acknowledgement_book, side),
            submit_to_ack_ms=latency,
        )

    def _record_entry_terminal_safety(self, status: Any) -> None:
        self.execution_latency_guard.record_terminal(status)

    async def _journal_call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        if self._order_journal.is_durable:
            return await asyncio.to_thread(method, *args, **kwargs)
        return method(*args, **kwargs)


def _quote_for_side(book: Any, side: Any) -> Optional[float]:
    if not isinstance(book, Mapping):
        return None
    return _number(book.get("best_ask" if str(side).upper() == "LONG" else "best_bid"))


def _opposite_side(side: Any) -> str:
    return "SHORT" if str(side).upper() == "LONG" else "LONG"


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
