"""
app/simulator.py
Simulated Execution & Virtual Portfolio Manager ($50 Bankroll).

Features:
- Enforces real network gas fees and real exchange fees (via fee_model)
- Sizes orders respecting $50 total portfolio (max 50% per opportunity)
- Tracks open positions, executed trades, cash balance, and equity
- Executes Dynamic Rebalancing: automatically exits positions when spread normalizes
- Persists all positions, trades, and portfolio history to PostgreSQL
"""

import asyncio
import logging
import time
from typing import Dict, List, Any, Optional
from app.config import settings
from app.database import db
from app.fee_model import real_fees

logger = logging.getLogger("simulator")


class PortfolioSimulator:
    def __init__(self, initial_capital: float = 50.0):
        self.initial_capital: float = initial_capital
        self.cash: float = initial_capital
        self.locked_capital: float = 0.0
        self.total_pnl: float = 0.0
        self.open_positions: Dict[int, Dict[str, Any]] = {}
        self.trade_counter: int = 0
        self.position_counter: int = 0
        self.is_active: bool = True
        self.spread_threshold: float = settings.ARB_SPREAD_THRESHOLD

    async def initialize(self):
        """Loads state or records starting portfolio history, ensuring state continuity across deployments."""
        try:
            # 1. Restore position and trade counters to avoid ID collisions on redeployment
            max_pos_row = await db.fetchrow("SELECT COALESCE(MAX(id), 0) as max_id FROM simulated_positions")
            if max_pos_row:
                self.position_counter = int(max_pos_row.get("max_id") or 0)

            trade_cnt_row = await db.fetchrow("SELECT COUNT(*) as cnt FROM simulated_trades")
            if trade_cnt_row:
                self.trade_counter = int(trade_cnt_row.get("cnt") or 0)

            # 2. Restore open positions
            open_rows = await db.fetch("SELECT * FROM simulated_positions WHERE status = 'OPEN'")
            self.open_positions.clear()
            for pos in open_rows:
                pos_id = int(pos["id"])
                self.open_positions[pos_id] = {
                    "id": pos_id,
                    "event_id": pos["event_id"],
                    "event_title": pos["event_title"],
                    "position_type": pos.get("position_type", "LONG_BASKET"),
                    "entry_basket": float(pos["entry_basket"]),
                    "shares": float(pos["shares"]),
                    "notional": round(float(pos["shares"]) * float(pos["entry_basket"]), 4),
                    "entry_friction": round(float(pos["cost"]) - (float(pos["shares"]) * float(pos["entry_basket"])), 4),
                    "opened_at": time.time()
                }

            # 3. Check if there is an existing portfolio history record
            row = await db.fetchrow("SELECT * FROM portfolio_history ORDER BY id DESC LIMIT 1")
            if row:
                self.cash = float(row["cash"])
                self.locked_capital = float(row["locked_capital"])
                self.total_pnl = float(row["total_pnl"])
                logger.info(
                    f"Loaded existing portfolio state: Cash=${self.cash:.2f}, "
                    f"Equity=${row['total_equity']:.2f}, Open Positions={len(self.open_positions)}, "
                    f"Total Trades={self.trade_counter}"
                )
            else:
                await self.record_history_snapshot()
                logger.info(f"Initialized new portfolio with ${self.initial_capital:.2f} capital.")
        except Exception as e:
            logger.error(f"Error restoring simulator state: {e}")
            await self.record_history_snapshot()

    async def record_history_snapshot(self):
        """Saves current balance snapshot into PostgreSQL."""
        total_equity = round(self.cash + self.locked_capital, 4)
        await db.execute(
            """
            INSERT INTO portfolio_history (cash, locked_capital, total_equity, total_pnl, open_positions, recorded_at)
            VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
            """,
            round(self.cash, 4),
            round(self.locked_capital, 4),
            total_equity,
            round(self.total_pnl, 4),
            len(self.open_positions)
        )

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Returns live summary metrics for API and UI."""
        total_equity = round(self.cash + self.locked_capital, 4)
        return_pct = round(((total_equity - self.initial_capital) / self.initial_capital) * 100.0, 2)

        return {
            "initial_capital": self.initial_capital,
            "cash": round(self.cash, 2),
            "locked_capital": round(self.locked_capital, 2),
            "total_equity": round(total_equity, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_pct": return_pct,
            "win_rate": 100.0 if self.total_pnl > 0 else 0.0,
            "total_trades": self.trade_counter,
            "open_positions_count": len(self.open_positions),
            "is_running": self.is_active,
            "spread_threshold": self.spread_threshold
        }

    async def execute_simulated_basket_entry(
        self,
        event_id: str,
        event_title: str,
        basket_pricing: Dict[str, Any],
        net_spread: float
    ) -> bool:
        """
        Executes a simulated buy order for an underpriced basket.
        Enforces real Polygon gas costs and real Polymarket fees.
        """
        if not self.is_active:
            return False

        # Max allocation per trade: 50% of initial capital ($25)
        max_alloc = self.initial_capital * settings.MAX_POSITION_PCT
        available_cash = min(self.cash, max_alloc)

        basket_cost_per_share = basket_pricing["basket_ask_sum"]
        if available_cash < 5.0 or basket_cost_per_share <= 0:
            return False

        # Number of complete baskets to purchase
        shares = round(available_cash / basket_cost_per_share, 2)
        total_notional = round(shares * basket_cost_per_share, 4)

        # Calculate exact real fees
        fee_info = real_fees.calculate_effective_execution_cost(
            notional_usd=total_notional,
            is_taker=True,
            event_title=event_title,
            is_basket=True
        )
        total_entry_cost = round(total_notional + fee_info["total_friction_usd"], 4)

        if self.cash < total_entry_cost:
            return False

        # Deduct from cash & update locked capital
        self.cash -= total_entry_cost
        self.locked_capital += total_notional
        self.position_counter += 1
        pos_id = self.position_counter

        pos_data = {
            "id": pos_id,
            "event_id": event_id,
            "event_title": event_title,
            "position_type": "LONG_BASKET",
            "entry_basket": basket_cost_per_share,
            "shares": shares,
            "notional": total_notional,
            "entry_friction": fee_info["total_friction_usd"],
            "opened_at": time.time()
        }
        self.open_positions[pos_id] = pos_data

        # Persist position to PostgreSQL
        await db.execute(
            """
            INSERT INTO simulated_positions (
                id, event_id, event_title, position_type, entry_basket, 
                shares, cost, status, opened_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'OPEN', CURRENT_TIMESTAMP)
            """,
            pos_id,
            event_id,
            event_title,
            "LONG_BASKET",
            basket_cost_per_share,
            shares,
            total_entry_cost
        )

        # Record individual token simulated fills
        for outcome in basket_pricing["outcomes"]:
            self.trade_counter += 1
            await db.execute(
                """
                INSERT INTO simulated_trades (
                    position_id, token_id, outcome_name, side, price, shares, cost, executed_at
                ) VALUES ($1, $2, $3, 'BUY', $4, $5, $6, CURRENT_TIMESTAMP)
                """,
                pos_id,
                outcome["token_id"],
                outcome["outcome_name"],
                outcome["best_ask"],
                shares,
                round(shares * outcome["best_ask"], 4)
            )

        await self.record_history_snapshot()
        logger.info(
            f"[Trade Executed] Bought basket for '{event_title[:30]}': "
            f"{shares} shares @ ${basket_cost_per_share:.3f} | Cost: ${total_entry_cost:.2f} (Gas: ${fee_info['gas_cost_usd']:.4f})"
        )
        return True

    async def check_and_rebalance_positions(self, live_feed):
        """
        Dynamic Rebalancing: Checks open positions and exits when the basket
        normalizes toward $1.00 (or if trailing stop triggers).
        """
        closed_ids = []
        for pos_id, pos in list(self.open_positions.items()):
            event_id = pos["event_id"]
            current_pricing = live_feed.get_event_basket_pricing(event_id)
            if not current_pricing:
                continue

            current_basket_price = current_pricing["basket_bid_sum"]
            entry_price = pos["entry_basket"]

            # Exit condition: Basket has rebalanced to >= 0.995 (or profit >= 2.5%)
            if current_basket_price >= 0.995 or (current_basket_price - entry_price) >= 0.025:
                shares = pos["shares"]
                gross_revenue = round(shares * current_basket_price, 4)

                # Real exit fees
                fee_info = real_fees.calculate_effective_execution_cost(
                    notional_usd=gross_revenue,
                    is_taker=True,
                    event_title=pos["event_title"],
                    is_basket=True
                )
                net_revenue = round(gross_revenue - fee_info["total_friction_usd"], 4)
                realized_pnl = round(net_revenue - (pos["notional"] + pos["entry_friction"]), 4)

                # Credit cash and free locked capital
                self.cash += net_revenue
                self.locked_capital -= pos["notional"]
                self.total_pnl += realized_pnl
                closed_ids.append(pos_id)

                # Update position in PostgreSQL
                await db.execute(
                    """
                    UPDATE simulated_positions 
                    SET exit_basket = $1, realized_pnl = $2, status = 'CLOSED', closed_at = CURRENT_TIMESTAMP
                    WHERE id = $3
                    """,
                    current_basket_price,
                    realized_pnl,
                    pos_id
                )

                logger.info(
                    f"[Position Closed] Rebalanced '{pos['event_title'][:25]}': "
                    f"Sold @ ${current_basket_price:.3f} | PnL: ${realized_pnl:+.3f}"
                )

        for pid in closed_ids:
            self.open_positions.pop(pid, None)

        if closed_ids:
            await self.record_history_snapshot()

    async def reset_simulation(self):
        """Resets the virtual portfolio back to $50."""
        self.cash = self.initial_capital
        self.locked_capital = 0.0
        self.total_pnl = 0.0
        self.open_positions.clear()
        await db.execute("UPDATE simulated_positions SET status = 'CANCELLED', closed_at = CURRENT_TIMESTAMP WHERE status = 'OPEN'")
        await self.record_history_snapshot()
        logger.info("Simulator reset to initial $50.00 balance.")


simulator = PortfolioSimulator(initial_capital=settings.INITIAL_CAPITAL)
