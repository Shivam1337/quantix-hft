"""
app/arb_engine.py
Real-time NegRisk Combinatorial Arbitrage Engine.

- Evaluates live order book basket pricing across all monitored events
- Uses real fee calculations (real_fees) to determine net edge
- Persists opportunities to PostgreSQL
- Triggers simulated execution and dynamic rebalancing in real-time
"""

import asyncio
import logging
import time
from typing import Dict, List, Any
from app.config import settings
from app.database import db
from app.fee_model import real_fees
from app.live_feed import live_feed
from app.simulator import simulator

logger = logging.getLogger("arb_engine")


class ArbitrageEngine:
    def __init__(self):
        self.is_running: bool = False
        self.recent_opportunities: List[Dict[str, Any]] = []

    async def start(self):
        self.is_running = True
        logger.info("Starting real-time Arbitrage Engine...")
        self._task = asyncio.create_task(self._engine_loop())

    async def stop(self):
        self.is_running = False
        if hasattr(self, "_task") and self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Arbitrage Engine stopped.")

    async def _engine_loop(self):
        """Continuously scans live order books for arbitrage and checks open positions."""
        while self.is_running:
            try:
                # 1. Scan for NegRisk underpriced baskets
                await self.scan_for_opportunities()

                # 2. Check open positions for Dynamic Rebalancing
                await simulator.check_and_rebalance_positions(live_feed)

                await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in arbitrage engine loop: {e}")
                await asyncio.sleep(2.0)

    async def scan_for_opportunities(self):
        """Scans all monitored events for NegRisk basket violations."""
        for event_id in list(live_feed.monitored_events.keys()):
            pricing = live_feed.get_event_basket_pricing(event_id)
            if not pricing or pricing["basket_ask_sum"] <= 0:
                continue

            basket_ask_sum = pricing["basket_ask_sum"]
            gross_spread = round(1.00 - basket_ask_sum, 4)

            # Estimate real friction
            fee_info = real_fees.calculate_effective_execution_cost(
                notional_usd=25.0,  # Based on $25 max trade size
                is_taker=True,
                event_title=pricing["event_title"],
                is_basket=True
            )
            friction_rate = fee_info["friction_pct"] / 100.0
            net_spread = round(gross_spread - friction_rate, 4)

            is_actionable = net_spread >= simulator.spread_threshold

            opp_data = {
                "event_id": event_id,
                "event_title": pricing["event_title"],
                "outcomes_count": pricing["outcomes_count"],
                "basket_sum": basket_ask_sum,
                "gross_spread": gross_spread,
                "net_spread": net_spread,
                "actionable": is_actionable,
                "is_crypto": pricing.get("is_crypto", False),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(pricing["timestamp"]))
            }

            # Store in recent cache (deduplicated by event_id)
            self.recent_opportunities = [op for op in self.recent_opportunities if op["event_id"] != event_id]
            self.recent_opportunities.insert(0, opp_data)
            self.recent_opportunities = self.recent_opportunities[:25]

            # Persist to PostgreSQL if significant or actionable
            if abs(gross_spread) >= 0.005:
                await db.execute(
                    """
                    INSERT INTO arb_opportunities (
                        event_id, event_title, outcomes_count, basket_sum, 
                        gross_spread, net_spread, actionable, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_TIMESTAMP)
                    """,
                    event_id,
                    pricing["event_title"],
                    pricing["outcomes_count"],
                    basket_ask_sum,
                    gross_spread,
                    net_spread,
                    is_actionable
                )

            # Trigger simulated execution if actionable
            if is_actionable and simulator.is_active:
                # Check if we already have an open position for this event to avoid duplicate entries
                has_open = any(p["event_id"] == event_id for p in simulator.open_positions.values())
                if not has_open:
                    await simulator.execute_simulated_basket_entry(
                        event_id=event_id,
                        event_title=pricing["event_title"],
                        basket_pricing=pricing,
                        net_spread=net_spread
                    )


arb_engine = ArbitrageEngine()
