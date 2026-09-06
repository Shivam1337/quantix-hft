"""Terminal-only tests for conservative live-order safety primitives."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from app.core.execution.economics import calculate_arrival_time_executable_order
from app.core.execution.latency import ExecutionLatencyGuard
from app.core.execution.order_journal import OrderJournal
from app.core.execution.submission import LighterSubmissionReceipt


class SubmissionReceiptTests(unittest.TestCase):
    def test_rejects_hash_when_lighter_response_code_is_not_successful(self):
        receipt = LighterSubmissionReceipt.from_response(SimpleNamespace(
            code=400, message="invalid nonce", tx_hash="misleading-hash",
            predicted_execution_time_ms=300, volume_quota_remaining=7,
        ))

        self.assertFalse(receipt.success)
        self.assertIn("code=400", receipt.error)
        self.assertEqual("misleading-hash", receipt.tx_hash)
        self.assertTrue(receipt.uncertain)

    def test_accepts_only_code_and_hash_together(self):
        receipt = LighterSubmissionReceipt.from_response(SimpleNamespace(
            code=200, message="accepted", tx_hash="real-hash",
            predicted_execution_time_ms=300, volume_quota_remaining=7,
        ))

        self.assertTrue(receipt.success)
        self.assertEqual(300, receipt.predicted_execution_time_ms)
        self.assertEqual(7, receipt.volume_quota_remaining)


class ArrivalEconomicsTests(unittest.TestCase):
    def test_small_size_cannot_treat_a_one_dollar_price_offset_as_one_dollar_profit(self):
        plan = calculate_arrival_time_executable_order(
            side="LONG", bids=[], asks=[["100.0", "0.10000"]],
            target_price=110.0, target_exit_buffer_usd=1.0,
            minimum_net_profit_usd=1.0, estimated_cost_usd=0.0,
            latency_buffer_usd=0.0, notional_cap_usd=1_000.0,
            max_levels=1, liquidity_participation=1.0, slippage_buffer_usd=0.0,
        )

        self.assertEqual(10.0, plan.required_price_edge_usd)
        self.assertFalse(plan.meets_economics)
        self.assertEqual(0.0, plan.executable.size_btc)

    def test_projected_profit_includes_quote_buffer_and_uses_actual_size(self):
        plan = calculate_arrival_time_executable_order(
            side="LONG", bids=[], asks=[["100.0", "0.20000"]],
            target_price=120.0, target_exit_buffer_usd=1.0,
            minimum_net_profit_usd=1.0, estimated_cost_usd=0.0,
            latency_buffer_usd=3.0, notional_cap_usd=1_000.0,
            max_levels=1, liquidity_participation=1.0, slippage_buffer_usd=3.0,
        )

        self.assertTrue(plan.meets_economics)
        self.assertEqual(0.2, plan.executable.size_btc)
        self.assertEqual(8.0, plan.required_price_edge_usd)
        self.assertEqual(2.6, plan.projected_net_profit_usd)


class LatencyGuardTests(unittest.TestCase):
    def test_observed_tail_latency_overrides_the_three_hundred_ms_floor(self):
        guard = ExecutionLatencyGuard(maximum_arrival_ms=1_500.0)
        guard.record_acknowledgement(side="LONG", submit_quote=100.0, acknowledged_quote=101.0, submit_to_ack_ms=200.0)
        guard.record_acknowledgement(side="LONG", submit_quote=100.0, acknowledged_quote=103.0, submit_to_ack_ms=2_000.0)

        decision = guard.check_entry(
            signal_key="new", quote_age_ms=10.0,
            projected_net_profit_usd=2.0, minimum_net_profit_usd=1.0,
        )

        self.assertEqual(2_000.0, guard.arrival_budget_ms)
        self.assertEqual(3.0, guard.adverse_quote_buffer_usd)
        self.assertFalse(decision.allowed)
        self.assertEqual("ARRIVAL_LATENCY_EXCESSIVE", decision.reason)

    def test_slippage_circuit_requires_cooldown_and_a_fresh_signal(self):
        guard = ExecutionLatencyGuard(maximum_arrival_ms=5_000.0, circuit_cooldown_seconds=10.0)
        guard.note_signal("failed-signal")
        for _ in range(3):
            guard.record_terminal("canceled-too-much-slippage", now=100.0)

        blocked = guard.check_entry(
            signal_key="failed-signal", quote_age_ms=1.0,
            projected_net_profit_usd=1.0, minimum_net_profit_usd=0.1, now=105.0,
        )
        stale_signal = guard.check_entry(
            signal_key="failed-signal", quote_age_ms=1.0,
            projected_net_profit_usd=1.0, minimum_net_profit_usd=0.1, now=111.0,
        )
        fresh_signal = guard.check_entry(
            signal_key="fresh-signal", quote_age_ms=1.0,
            projected_net_profit_usd=1.0, minimum_net_profit_usd=0.1, now=111.0,
        )

        self.assertEqual("SLIPPAGE_CIRCUIT_OPEN", blocked.reason)
        self.assertEqual("SLIPPAGE_CIRCUIT_REQUIRES_FRESH_SIGNAL", stale_signal.reason)
        self.assertTrue(fresh_signal.allowed)


class OrderJournalTests(unittest.TestCase):
    def test_unresolved_intent_survives_restart_and_client_indices_do_not_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "orders.db")
            journal = OrderJournal(path)
            first = journal.reserve_intent(
                trade_id=1, phase="ENTRY", side="LONG", size_btc=0.001,
                limit_price=80_000.0, submitted_at=1_700_000_000.0,
            )
            journal.acknowledge(first, tx_hash="tx", response_code=200, response_message="accepted")
            journal.mark_position_open(first, terminal_status="filled")

            restarted = OrderJournal(path)
            pending = restarted.unresolved_orders()
            second = restarted.reserve_intent(
                trade_id=2, phase="ENTRY", side="SHORT", size_btc=0.001,
                limit_price=80_001.0, submitted_at=1_700_000_001.0,
            )
            restarted.record_terminal(first, terminal_status="canceled-too-much-slippage")

            self.assertEqual(1, len(pending))
            self.assertEqual(first, pending[0].client_order_index)
            self.assertEqual("POSITION_OPEN", pending[0].state)
            self.assertGreater(second, first)
            self.assertEqual([second], [item.client_order_index for item in restarted.unresolved_orders()])


if __name__ == "__main__":
    unittest.main()
