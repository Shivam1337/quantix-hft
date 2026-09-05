"""
High-Frequency Market Making Discrete-Event Simulator.
Simulates order transit latency, queue priority, post-only fills against real market trades,
inventory limits, maker fees/rebates, and adverse selection.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import numpy as np
from signals import OrderBook, BookLevel, AvellanedaStoikovModel, RollingVolatility, calculate_ofi


@dataclass
class Fill:
    timestamp: float
    side: str          # 'BUY' (we bought) or 'SELL' (we sold)
    price: float
    size: float
    fee: float
    inventory_after: float
    cash_after: float
    mid_at_fill: float
    pnl_1s_after: Optional[float] = None
    pnl_5s_after: Optional[float] = None


@dataclass
class ActiveQuote:
    bid_price: float
    ask_price: float
    size: float
    activated_at: float  # timestamp after simulated network transit latency


class HFMarketMakingSimulator:
    """
    Simulates high-frequency market making execution against real tick-by-tick market data.
    """

    def __init__(
        self,
        strategy_name: str,
        model: AvellanedaStoikovModel,
        initial_capital: float = 1000.0,
        order_size_notional: float = 50.0,  # $50 quotes
        maker_fee_rate: float = 0.0001,    # 0.01% maker fee (or negative for rebates)
        latency_ms: float = 10.0,          # Simulated 10ms one-way transit/execution latency
        max_inventory_notional: float = 300.0, # Circuit breaker max position size
        use_inventory_skew: bool = True,
        use_ofi: bool = True
    ):
        self.strategy_name = strategy_name
        self.model = model
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.inventory = 0.0  # In base asset contracts
        self.order_size_notional = order_size_notional
        self.maker_fee_rate = maker_fee_rate
        self.latency_seconds = latency_ms / 1000.0
        self.max_inventory_notional = max_inventory_notional
        self.use_inventory_skew = use_inventory_skew
        self.use_ofi = use_ofi

        self.vol_estimator = RollingVolatility(window_size=50)
        self.current_book: Optional[OrderBook] = None
        self.prev_book: Optional[OrderBook] = None
        self.pending_quote: Optional[ActiveQuote] = None
        self.active_quote: Optional[ActiveQuote] = None

        self.fills: List[Fill] = []
        self.equity_history: List[Tuple[float, float, float, float]] = [] # (time, equity, inventory, mid_price)
        self.unfilled_quotes_count = 0
        self.total_quotes_count = 0

    def get_equity(self, mid_price: float) -> float:
        return self.cash + (self.inventory * mid_price)

    def on_book_update(self, new_book: OrderBook):
        """Processes an L2 book update event."""
        mid = new_book.mid_price
        if mid <= 0:
            return

        vol = self.vol_estimator.update(mid)
        ofi = 0.0
        if self.current_book is not None:
            ofi = calculate_ofi(self.current_book, new_book, levels=3)

        self.prev_book = self.current_book
        self.current_book = new_book

        # Activate pending quotes if transit latency has elapsed
        if self.pending_quote and new_book.timestamp >= self.pending_quote.activated_at:
            self.active_quote = self.pending_quote
            self.pending_quote = None

        # Check inventory risk limits
        current_inv_notional = abs(self.inventory * mid)
        if current_inv_notional >= self.max_inventory_notional:
            # Circuit breaker: stop quoting on the accumulating side
            can_quote_bid = (self.inventory < 0)
            can_quote_ask = (self.inventory > 0)
        else:
            can_quote_bid = True
            can_quote_ask = True

        # Generate target quotes
        bid, ask = self.model.generate_quotes(
            book=new_book,
            inventory=self.inventory,
            volatility=vol,
            ofi=ofi,
            use_inventory_skew=self.use_inventory_skew,
            use_ofi=self.use_ofi
        )

        quote_qty = round(self.order_size_notional / mid, 4)

        # Place pending quote with transit delay
        self.pending_quote = ActiveQuote(
            bid_price=bid if can_quote_bid else 0.0,
            ask_price=ask if can_quote_ask else float('inf'),
            size=quote_qty,
            activated_at=new_book.timestamp + self.latency_seconds
        )
        self.total_quotes_count += 1

        # Track adverse selection on pending fills
        self._update_adverse_selection(new_book.timestamp, mid)

        # Record equity snapshot
        equity = self.get_equity(mid)
        self.equity_history.append((new_book.timestamp, equity, self.inventory, mid))

    def on_trade(self, timestamp: float, price: float, size: float, side: str):
        """
        Processes a real-world market trade event against our active quotes.
        side: 'B' = Taker Buy (hits ask), 'A' = Taker Sell (hits bid)
        """
        if not self.active_quote:
            return

        mid = self.current_book.mid_price if self.current_book else price

        # If a real-world taker bought at price >= our ask, our passive ask fills
        if side == 'B' and price >= self.active_quote.ask_price:
            fill_sz = min(self.active_quote.size, size)
            fill_px = self.active_quote.ask_price
            notional = fill_sz * fill_px
            fee = notional * self.maker_fee_rate

            self.cash += (notional - fee)
            self.inventory -= fill_sz

            fill = Fill(
                timestamp=timestamp,
                side='SELL',
                price=fill_px,
                size=fill_sz,
                fee=fee,
                inventory_after=self.inventory,
                cash_after=self.cash,
                mid_at_fill=mid
            )
            self.fills.append(fill)

            # Invalidate quote until replaced
            self.active_quote = None

        # If a real-world taker sold at price <= our bid, our passive bid fills
        elif side == 'A' and price <= self.active_quote.bid_price:
            fill_sz = min(self.active_quote.size, size)
            fill_px = self.active_quote.bid_price
            notional = fill_sz * fill_px
            fee = notional * self.maker_fee_rate

            self.cash -= (notional + fee)
            self.inventory += fill_sz

            fill = Fill(
                timestamp=timestamp,
                side='BUY',
                price=fill_px,
                size=fill_sz,
                fee=fee,
                inventory_after=self.inventory,
                cash_after=self.cash,
                mid_at_fill=mid
            )
            self.fills.append(fill)

            # Invalidate quote until replaced
            self.active_quote = None

    def _update_adverse_selection(self, current_time: float, current_mid: float):
        """Measures whether the market moved against our fills 1s and 5s later."""
        for fill in self.fills[-20:]:  # Check recent fills
            time_elapsed = current_time - fill.timestamp
            if fill.pnl_1s_after is None and time_elapsed >= 1.0:
                if fill.side == 'BUY':
                    fill.pnl_1s_after = (current_mid - fill.price) / fill.price * 10000.0  # in bps
                else:
                    fill.pnl_1s_after = (fill.price - current_mid) / fill.price * 10000.0

            if fill.pnl_5s_after is None and time_elapsed >= 5.0:
                if fill.side == 'BUY':
                    fill.pnl_5s_after = (current_mid - fill.price) / fill.price * 10000.0
                else:
                    fill.pnl_5s_after = (fill.price - current_mid) / fill.price * 10000.0

    def get_stats(self) -> Dict[str, float]:
        """Calculates quantitative performance metrics."""
        if not self.equity_history:
            return {}

        equities = np.array([e[1] for e in self.equity_history])
        final_mid = self.equity_history[-1][3]
        final_equity = self.get_equity(final_mid)
        net_pnl = final_equity - self.initial_capital
        return_pct = (net_pnl / self.initial_capital) * 100.0

        # High-frequency returns
        eq_returns = np.diff(equities) / equities[:-1]
        std_returns = np.std(eq_returns) if len(eq_returns) > 1 else 0.0
        # Annualized Sharpe (assuming ~10 updates/sec * 86400 * 365)
        annualization_factor = np.sqrt(10 * 86400 * 365)
        sharpe = (np.mean(eq_returns) / std_returns * annualization_factor) if std_returns > 1e-8 else 0.0

        # Max Drawdown
        cum_max = np.maximum.accumulate(equities)
        drawdowns = (cum_max - equities) / cum_max
        max_drawdown = float(np.max(drawdowns)) * 100.0

        # Inventory statistics
        inventories = np.array([e[2] for e in self.equity_history])
        inv_std = float(np.std(inventories))
        max_inv = float(np.max(np.abs(inventories)))

        # Adverse selection metrics (in basis points)
        pnl_1s = [f.pnl_1s_after for f in self.fills if f.pnl_1s_after is not None]
        avg_adv_sel_1s = float(np.mean(pnl_1s)) if pnl_1s else 0.0

        pnl_5s = [f.pnl_5s_after for f in self.fills if f.pnl_5s_after is not None]
        avg_adv_sel_5s = float(np.mean(pnl_5s)) if pnl_5s else 0.0

        total_fees = sum(f.fee for f in self.fills)

        return {
            "Strategy": self.strategy_name,
            "Initial Capital ($)": self.initial_capital,
            "Final Equity ($)": round(final_equity, 3),
            "Net PnL ($)": round(net_pnl, 3),
            "Return (%)": round(return_pct, 3),
            "Sharpe Ratio": round(sharpe, 2),
            "Max Drawdown (%)": round(max_drawdown, 3),
            "Total Fills": len(self.fills),
            "Total Fees Paid ($)": round(total_fees, 3),
            "Final Inventory": round(self.inventory, 4),
            "Max Absolute Inventory": round(max_inv, 4),
            "Inventory StdDev": round(inv_std, 4),
            "Avg Post-Fill Return 1s (bps)": round(avg_adv_sel_1s, 2),
            "Avg Post-Fill Return 5s (bps)": round(avg_adv_sel_5s, 2),
        }
