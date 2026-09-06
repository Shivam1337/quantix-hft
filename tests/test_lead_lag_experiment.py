import asyncio
import copy
import unittest

from analyze_experiment import fixed_horizon_paper_study
from app.core.lead_lag_analyzer import LeadLagAnalyzer
from app.core.resource_monitor import ResourceMonitor
from app.core.sniper_engine import SniperEngine
from app.core.state_manager import StateManager


class RecordingDerivedStore:
    """In-memory persistence spy used to prove feed callbacks do not store raw messages."""

    def __init__(self, snapshot=None):
        self.snapshot = snapshot or {"chart_samples": [], "closed_trades": [], "repricing_events": []}
        self.chart_samples = []
        self.trades = []
        self.events = []
        self.decisions = []
        self.started = False
        self.stopped = False

    async def start(self, **_kwargs):
        self.started = True
        return copy.deepcopy(self.snapshot)

    async def stop(self):
        self.stopped = True

    def record_chart_sample(self, sample):
        self.chart_samples.append(copy.deepcopy(sample))

    def record_trade(self, trade):
        self.trades.append(copy.deepcopy(trade))

    def record_event(self, event):
        self.events.append(copy.deepcopy(event))

    def record_decision(self, decision):
        self.decisions.append(copy.deepcopy(decision))

    def stats(self):
        return {"backend": "test", "connected": self.started, "stores_raw_incoming_messages": False}


class LeadLagExperimentTests(unittest.TestCase):
    def setUp(self):
        from app.core.settings_manager import settings_manager
        settings_manager.update_settings({
            "simulation_starting_balance": 100.0,
            "trade_margin_fraction": 0.50,
            "leverage": 50.0,
            "min_lag_trigger": 6.0,
        })

    @staticmethod
    def tick(analyzer, now, bn=100.0, by=100.0, ok=100.0, hl=100.0, lighter=100.0, poly=100.0):
        return analyzer.process_tick(bn, by, ok, hl, lighter, poly, now=now)

    def test_api_style_snapshot_does_not_mutate_histories_or_basis(self):
        analyzer = LeadLagAnalyzer()
        self.tick(analyzer, 0.0)
        self.tick(analyzer, 0.3)
        self.tick(analyzer, 3.0, bn=110.0, by=110.0, ok=110.0, hl=110.0, lighter=100.0)

        history_lengths = {venue: len(history) for venue, history in analyzer.histories.items()}
        basis_before = copy.deepcopy(analyzer.venue_basis)
        snapshot = analyzer.get_latest()
        snapshot["venue_basis"]["Binance"] = 999.0

        self.assertEqual(history_lengths, {venue: len(history) for venue, history in analyzer.histories.items()})
        self.assertEqual(basis_before, analyzer.venue_basis)
        self.assertNotEqual(analyzer.get_latest()["venue_basis"]["Binance"], 999.0)

    def test_leader_reversal_is_not_reported_as_lighter_catchup(self):
        analyzer = LeadLagAnalyzer()
        self.tick(analyzer, 0.0)
        self.tick(analyzer, 0.3)
        started = self.tick(analyzer, 3.0, bn=110.0, by=110.0, ok=110.0, hl=110.0, lighter=100.0)
        self.assertEqual("STARTED", started["event_transition"]["type"])

        closed = self.tick(analyzer, 4.0)
        self.assertEqual("SPREAD_CLOSED", closed["event_transition"]["type"])
        event = analyzer.get_repricing_events()[0]
        self.assertEqual("LEADER_REVERSAL", event["resolution_type"])
        self.assertTrue(event["spread_closed"])

    def test_polymarket_move_cannot_nominate_a_primary_signal(self):
        analyzer = LeadLagAnalyzer()
        self.tick(analyzer, 0.0)
        self.tick(analyzer, 0.3)
        result = self.tick(analyzer, 3.0, poly=120.0)

        self.assertNotEqual("Polymarket", result["dynamic_leader"])
        self.assertFalse(result["signal_eligible"])
        self.assertEqual("MODERATE", result["consensus_status"])

    def test_only_the_updated_feed_extends_its_price_history(self):
        analyzer = LeadLagAnalyzer()
        analyzer.process_tick(100.0, 100.0, 100.0, 100.0, 100.0, 100.0, now=0.0, updated_venue="Binance")
        self.assertEqual(1, len(analyzer.histories["Binance"]))
        self.assertEqual(0, len(analyzer.histories["Bybit"]))

        analyzer.process_tick(100.0, 100.0, 100.0, 100.0, 100.1, 100.0, now=0.1, updated_venue="Lighter.xyz")
        self.assertEqual(1, len(analyzer.histories["Binance"]))
        self.assertEqual(0, len(analyzer.histories["Bybit"]))

    def test_basis_is_frozen_during_a_major_breakout(self):
        analyzer = LeadLagAnalyzer()
        self.tick(analyzer, 0.0)
        self.tick(analyzer, 0.3)
        self.tick(analyzer, 3.0, bn=110.0, by=110.0, ok=110.0, hl=110.0, lighter=100.0)

        self.assertEqual(0.0, analyzer.venue_basis["Binance"])

    def test_paper_engine_rejects_single_venue_signal_without_consensus(self):
        engine = SniperEngine()
        result = engine.process_tick(
            {"best_bid": 100.0, "best_ask": 100.1, "mid_price": 100.05},
            "Binance",
            110.0,
            110.0,
            5.0,
            "MODERATE",
            "Only Binance moved.",
            venue_prices={"Binance": 110.0},
        )

        self.assertIsNone(result["active_position"])
        self.assertEqual("INSUFFICIENT_MAJOR_CONSENSUS", result["decision"]["rejection_reason"])

    def test_derived_persistence_never_receives_raw_quote_or_orderbook_messages(self):
        persistence = RecordingDerivedStore()
        manager = StateManager(persistence=persistence)
        manager.update_binance([[100.0, 5.0]], [[101.0, 4.0]], 100.0, 101.0)

        self.assertEqual(1, len(persistence.chart_samples))
        chart_sample = persistence.chart_samples[0]
        self.assertEqual(100.5, chart_sample["binance"])
        self.assertNotIn("bids", chart_sample)
        self.assertNotIn("asks", chart_sample)
        self.assertNotIn("exchange_timestamp_ms", chart_sample)
        self.assertEqual([], persistence.trades)

    def test_fixed_horizon_study_uses_executable_opposite_side_quote(self):
        records = [
            {
                "record_type": "lead_lag_event",
                "transition": "STARTED",
                "recorded_at_monotonic_ns": 1_000_000_000,
                "event": {"direction": "UPWARD_CATCHUP"},
                "execution_context": {"lighter": {"ask": 100.0, "bid": 99.9}},
            },
            {
                "record_type": "quote",
                "venue": "Lighter.xyz",
                "receive_monotonic_ns": 1_050_000_000,
                "bid": 101.0,
                "ask": 101.1,
            },
            {
                "record_type": "quote",
                "venue": "Lighter.xyz",
                "receive_monotonic_ns": 1_100_000_000,
                "bid": 102.0,
                "ask": 102.1,
            },
        ]

        study = fixed_horizon_paper_study(records)
        self.assertEqual(1, study["horizons_ms"]["50"]["observations"])
        self.assertEqual(1.0, study["horizons_ms"]["50"]["avg_pnl_per_btc_before_costs"])
        self.assertEqual(2.0, study["horizons_ms"]["100"]["avg_pnl_per_btc_before_costs"])

    def test_chart_samples_start_with_any_fresh_provider_and_preserve_missing_gaps(self):
        manager = StateManager(persistence=RecordingDerivedStore())
        manager.update_binance([[100.0, 1.0]], [[101.0, 1.0]], 100.0, 101.0)
        snapshot = manager.get_full_state()

        self.assertEqual(1, snapshot["chart"]["sample_count"])
        self.assertEqual([100.5], snapshot["chart"]["binance_series"])
        self.assertEqual([None], snapshot["chart"]["lighter_series"])
        self.assertEqual([None], snapshot["chart"]["lighter_lag_series"])

        binance = next(
            provider for provider in snapshot["provider_insights"]["providers"]
            if provider["id"] == "binance"
        )
        self.assertEqual("FRESH", binance["data_quality"])
        self.assertTrue(binance["signal_eligible"])

    def test_persisted_charts_and_trades_restore_before_live_feeds_arrive(self):
        persisted_trade = {
            "id": 10,
            "time": "09:30:00",
            "side": "LONG",
            "leader": "Binance",
            "size": 0.05,
            "size_btc": 0.05,
            "entry_px": 100.0,
            "exit_px": 102.0,
            "gross_pnl": 0.1,
            "fees_paid": 0.0,
            "net_pnl": 0.1,
            "hold_sec": 1.2,
            "reason": "TARGET_REACHED",
            "is_win": True,
        }
        persistence = RecordingDerivedStore(
            {
                "chart_samples": [{"time": "2026-09-06T00:00:00+00:00", "binance": 100.0, "lighter": 99.0, "l_lag": -1.0}],
                "closed_trades": [persisted_trade],
                "repricing_events": [],
            }
        )
        manager = StateManager(persistence=persistence)
        asyncio.run(manager.initialize_persistence())
        snapshot = manager.get_full_state()

        self.assertTrue(persistence.started)
        self.assertEqual(1, snapshot["chart"]["sample_count"])
        self.assertEqual(10, snapshot["recent_trades"][0]["id"])
        self.assertEqual(1, snapshot["trading_performance"]["total_trades"])
        asyncio.run(manager.shutdown())
        self.assertTrue(persistence.stopped)

    def test_graceful_shutdown_audits_open_paper_trade_without_fabricating_pnl(self):
        """A deploy may interrupt a paper trade, but must never turn it into a result."""
        persistence = RecordingDerivedStore()
        manager = StateManager(persistence=persistence)
        open_trade = {
            "id": 7,
            "side": "LONG",
            "leader_name": "Binance",
            "size": 0.05,
            "entry_px": 100.0,
            "target_px": 106.0,
            "expected_lag": 6.0,
            "entry_ts": 1.0,
        }
        manager.sniper_engine.active_trade = copy.deepcopy(open_trade)

        # Once shutdown begins, a late WebSocket callback cannot mutate the engine.
        manager.begin_shutdown()
        manager.update_binance([[100.0, 1.0]], [[101.0, 1.0]], 100.0, 101.0)
        self.assertEqual(0, manager.messages_count)

        asyncio.run(manager.shutdown())

        self.assertTrue(persistence.stopped)
        self.assertIsNone(manager.sniper_engine.active_trade)
        self.assertEqual([], list(manager.sniper_engine.closed_trades))
        self.assertEqual(0, manager.sniper_engine.get_performance()["total_trades"])
        shutdown_events = [event for event in persistence.events if event["transition"] == "PROCESS_SHUTDOWN"]
        self.assertEqual(1, len(shutdown_events))
        audited_trade = shutdown_events[0]["event"]["trade"]
        self.assertEqual(open_trade, audited_trade)
        self.assertNotIn("exit_px", audited_trade)
        self.assertNotIn("net_pnl", audited_trade)
        self.assertFalse(shutdown_events[0]["event"]["realized_pnl_recorded"])
        self.assertTrue(manager.get_health()["shutting_down"])
        self.assertTrue(manager.is_shutting_down())
        self.assertFalse(manager.get_readiness()["ready"])

        # Lifespan cleanup may be entered more than once; it must not create a
        # second audit event or try to close the already-drained store again.
        asyncio.run(manager.shutdown())
        self.assertEqual(1, len([event for event in persistence.events if event["transition"] == "PROCESS_SHUTDOWN"]))

    def test_resource_monitor_reports_comparable_host_and_process_fields(self):
        monitor = ResourceMonitor(min_sample_interval_seconds=0.0)
        first = monitor.snapshot()
        second = monitor.snapshot(force=True)

        self.assertGreaterEqual(first["logical_cpu_count"], 1)
        for field in (
            "system_cpu_percent",
            "process_cpu_percent",
            "system_memory_percent",
            "process_memory_percent",
        ):
            value = second[field]
            if value is not None:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 100.0)

        self.assertIn("system_memory_total_bytes", second)
    def test_dynamic_capital_management_and_50x_leverage(self):
        engine = SniperEngine()
        self.assertEqual(100.0, engine.base_balance_usd)
        self.assertEqual(0.50, engine.margin_fraction)
        self.assertEqual(50.0, engine.leverage)

        # Baseline sizing at $90,000 BTC
        calc = engine.calculate_trade_size(90000.0)
        self.assertEqual(50.0, calc["margin_allocated_usd"])
        self.assertEqual(2500.0, calc["notional_usd"])
        self.assertEqual(50.0, calc["leverage"])
        self.assertEqual(0.0278, calc["size_btc"])
        self.assertEqual(100.0, calc["account_balance_usd"])

        # Baseline sizing at $80,000 BTC ($2500 / 80000 = 0.03125 -> 0.0312)
        calc_80k = engine.calculate_trade_size(80000.0)
        self.assertEqual(0.0312, calc_80k["size_btc"])
        self.assertEqual(2500.0, calc_80k["notional_usd"])

        # Check performance accounting without trades
        perf = engine.get_performance()
        self.assertEqual(100.0, perf["account_base_balance_usd"])
        self.assertEqual(100.0, perf["account_balance_usd"])
        self.assertEqual(100.0, perf["account_equity_usd"])
        self.assertEqual(0.0, perf["margin_used_usd"])
        self.assertEqual(100.0, perf["free_margin_usd"])
        self.assertEqual(50.0, perf["leverage"])
        self.assertEqual(2500.0, perf["target_notional_usd"])

        # Simulate a closed winning trade (+$0.50 net pnl)
        engine.closed_trades.appendleft({
            "id": 1,
            "time": "12:00:00",
            "side": "LONG",
            "size": 0.0278,
            "size_btc": 0.0278,
            "entry_px": 90000.0,
            "exit_px": 90018.0,
            "gross_pnl": 0.50,
            "net_pnl": 0.50,
            "hold_sec": 1.5,
            "reason": "TARGET_REACHED",
            "is_win": True,
            "margin_allocated_usd": 50.0,
            "leverage": 50.0,
            "notional_usd": 2500.0,
        })

        perf_after = engine.get_performance()
        self.assertEqual(100.50, perf_after["account_balance_usd"])
        self.assertEqual(100.50, perf_after["account_equity_usd"])
        self.assertEqual(1.0, perf_after["return_on_margin_pct"])  # $0.50 / $50.00 = 1.0%

        # Next trade dynamically adjusts margin and notional based on growing balance
        calc_next = engine.calculate_trade_size(90000.0)
        self.assertEqual(50.25, calc_next["margin_allocated_usd"])
        self.assertEqual(2512.50, calc_next["notional_usd"])


if __name__ == "__main__":
    unittest.main()
