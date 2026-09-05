"""
Microstructure Signal Engine
Implements Order Book reconstruction, Order Flow Imbalance (OFI),
micro-price calculations, and Avellaneda-Stoikov reservation pricing.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import math
import numpy as np


@dataclass
class BookLevel:
    price: float
    size: float
    orders: int = 1


@dataclass
class OrderBook:
    bids: List[BookLevel]  # sorted descending by price
    asks: List[BookLevel]  # sorted ascending by price
    timestamp: float = 0.0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def best_bid_size(self) -> float:
        return self.bids[0].size if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0].size if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid == 0.0:
            return 0.0
        return (self.spread / mid) * 10000.0

    @property
    def micro_price(self) -> float:
        """Volume-weighted micro-price using top-of-book depth."""
        if not self.bids or not self.asks:
            return self.mid_price
        total_sz = self.best_bid_size + self.best_ask_size
        if total_sz == 0.0:
            return self.mid_price
        return (self.best_bid * self.best_ask_size + self.best_ask * self.best_bid_size) / total_sz


def calculate_ofi(prev_book: OrderBook, curr_book: OrderBook, levels: int = 3) -> float:
    """
    Computes Cont-Kukanov-Stoikov Order Flow Imbalance (OFI) normalized to [-100.0, +100.0].
    Evaluates order book shifts in USD notional (price * size) so that it is scale-invariant
    across high-price assets (BTC) and micro-price meme tokens (BOME).
    Positive OFI indicates net incoming buying pressure (depth building on bid / depleted on ask).
    Negative OFI indicates net selling pressure.
    """
    if not prev_book.bids or not prev_book.asks or not curr_book.bids or not curr_book.asks:
        return 0.0

    depth = min(levels, len(prev_book.bids), len(curr_book.bids), len(prev_book.asks), len(curr_book.asks))
    if depth == 0:
        return 0.0

    delta_b_usd = 0.0
    delta_a_usd = 0.0
    total_depth_usd = 0.0

    for i in range(depth):
        p_b_prev = prev_book.bids[i].price
        q_b_prev = prev_book.bids[i].size * p_b_prev
        p_b_curr = curr_book.bids[i].price
        q_b_curr = curr_book.bids[i].size * p_b_curr

        if p_b_curr > p_b_prev:
            delta_b_usd += q_b_curr
        elif p_b_curr == p_b_prev:
            delta_b_usd += (q_b_curr - q_b_prev)
        else:
            delta_b_usd -= q_b_prev

        p_a_prev = prev_book.asks[i].price
        q_a_prev = prev_book.asks[i].size * p_a_prev
        p_a_curr = curr_book.asks[i].price
        q_a_curr = curr_book.asks[i].size * p_a_curr

        if p_a_curr < p_a_prev:
            delta_a_usd += q_a_curr
        elif p_a_curr == p_a_prev:
            delta_a_usd += (q_a_curr - q_a_prev)
        else:
            delta_a_usd -= q_a_prev

        total_depth_usd += (q_b_curr + q_a_curr)

    if total_depth_usd <= 0:
        return 0.0

    ofi_ratio = (delta_b_usd - delta_a_usd) / total_depth_usd
    return float(np.clip(ofi_ratio * 100.0, -100.0, 100.0))


class RollingVolatility:
    """Maintains a rolling window of mid-price logarithmic returns to estimate local volatility."""

    def __init__(self, window_size: int = 50):
        self.window_size = window_size
        self.prices: List[float] = []

    def update(self, mid_price: float) -> float:
        if mid_price <= 0:
            return 0.0001
        self.prices.append(mid_price)
        if len(self.prices) > self.window_size:
            self.prices.pop(0)

        if len(self.prices) < 5:
            return 0.001

        returns = np.diff(np.log(self.prices))
        vol = float(np.std(returns))
        return max(vol, 1e-6)


class AvellanedaStoikovModel:
    """
    Avellaneda-Stoikov Market Making Model with OFI Skew Extension.
    Calculates inventory-penalized reservation price and optimal half-spread.
    """

    def __init__(
        self,
        gamma: float = 0.5,           # Risk aversion parameter
        kappa: float = 1.5,           # Liquidity sensitivity
        beta_ofi: float = 0.5,        # Sensitivity to OFI (fraction of half-spread)
        tick_size: float = 0.0001,    # Minimum price tick
        min_spread_bps: float = 2.0,  # Target half-spread in bps
        max_inventory_usd: float = 25.0  # Max inventory for normalization ($50 capital scale)
    ):
        self.gamma = gamma
        self.kappa = kappa
        self.beta_ofi = beta_ofi
        self.tick_size = tick_size
        self.min_spread_bps = min_spread_bps
        self.max_inventory_usd = max_inventory_usd

    def compute_reservation_price(
        self,
        mid_price: float,
        inventory: float,
        volatility: float,
        ofi: float = 0.0,
        market_spread: float = 0.0,
        use_inventory_skew: bool = True,
        use_ofi: bool = True
    ) -> float:
        """
        Normalized scale-invariant reservation price:
        r(s, q) = s * [1 - q_norm * gamma * vol_factor + ofi_skew]
        """
        r = mid_price

        # Normalized inventory in [-1, +1]
        inv_usd = inventory * mid_price
        q_norm = np.clip(inv_usd / max(self.max_inventory_usd, 1.0), -1.0, 1.0)

        # Inventory penalty skew
        if use_inventory_skew and q_norm != 0.0:
            # Shift reservation price by a fraction of volatility / spread
            inv_shift = q_norm * self.gamma * max(volatility, 0.0005) * mid_price
            r -= inv_shift

        # Order Flow Imbalance skew
        if use_ofi and ofi != 0.0:
            # Bound OFI using hyperbolic tangent to avoid extreme outlier shifts
            ofi_factor = math.tanh(ofi / 50.0)
            base_spread = max(market_spread, mid_price * (self.min_spread_bps / 10000.0))
            ofi_shift = ofi_factor * self.beta_ofi * (base_spread * 0.5)
            r += ofi_shift

        return r

    def compute_optimal_spread(
        self,
        mid_price: float,
        volatility: float,
        market_spread: float = 0.0
    ) -> float:
        """
        Dynamic half-spread responsive to local volatility and market spread.
        """
        min_half_spread = mid_price * (self.min_spread_bps / 10000.0)
        vol_half_spread = mid_price * (self.gamma * max(volatility, 0.0002))

        # Respect market spread if available (quote inside or at top of book)
        if market_spread > 0:
            market_half_spread = market_spread * 0.45
            half_spread = max(market_half_spread, min_half_spread, vol_half_spread)
        else:
            half_spread = max(min_half_spread, vol_half_spread)

        return max(half_spread, self.tick_size)

    def generate_quotes(
        self,
        book: OrderBook,
        inventory: float,
        volatility: float,
        ofi: float = 0.0,
        use_inventory_skew: bool = True,
        use_ofi: bool = True
    ) -> Tuple[float, float]:
        """
        Generates post-only bid and ask quotes aligned to tick size.
        Ensures bid < best_ask and ask > best_bid to maintain passive quoting.
        """
        mid = book.mid_price
        spread = book.spread
        r = self.compute_reservation_price(
            mid_price=mid,
            inventory=inventory,
            volatility=volatility,
            ofi=ofi,
            market_spread=spread,
            use_inventory_skew=use_inventory_skew,
            use_ofi=use_ofi
        )
        half_spread = self.compute_optimal_spread(mid, volatility, market_spread=spread)

        raw_bid = r - half_spread
        raw_ask = r + half_spread

        # Snap to tick size
        bid = math.floor(raw_bid / self.tick_size) * self.tick_size
        ask = math.ceil(raw_ask / self.tick_size) * self.tick_size

        # Guard post-only passive constraints (cannot cross the book)
        if book.best_ask > 0:
            bid = min(bid, book.best_ask - self.tick_size)
        if book.best_bid > 0:
            ask = max(ask, book.best_bid + self.tick_size)

        if bid >= ask:
            ask = bid + self.tick_size

        decimals = max(2, min(8, int(round(-math.log10(self.tick_size))))) if self.tick_size < 1 else 2
        return round(bid, decimals), round(ask, decimals)

    def compute_dynamic_sizes(
        self,
        mid_price: float,
        max_order_size_usd: float,
        min_order_size_usd: float,
        inventory_usd: float,
        max_inventory_usd: float,
        ofi: float = 0.0,
        book_imbalance: float = 0.0,
        pair_pnl: float = 0.0
    ) -> Tuple[float, float, float, float]:
        """
        Computes asymmetric dynamic quote sizes for bid and ask sides based on:
        1. Profit momentum: Scaling up when pair is profitable, contracting in drawdowns.
        2. Order book pressure: Positive OFI / bid depth expands bid & shrinks ask; negative OFI expands ask & shrinks bid.
        3. Inventory headroom: Strictly clamps sizes so fills cannot breach max inventory.

        Returns:
            (bid_qty, ask_qty, bid_usd, ask_usd)
        """
        if mid_price <= 0:
            return 0.0, 0.0, 0.0, 0.0

        # Baseline notional size (70% of max size)
        base_size = max(max_order_size_usd * 0.70, min_order_size_usd)

        # 1. Profit Momentum Multiplier
        # In profit (e.g. +$1.50), scales up to 1.35x. In drawdown (-$1.00), scales down to 0.65x
        if pair_pnl >= 0:
            profit_mult = 1.0 + 0.35 * math.tanh(pair_pnl / 1.5)
        else:
            profit_mult = 1.0 - 0.35 * math.tanh(abs(pair_pnl) / 1.0)

        # 2. Combined Order Book Pressure in [-1, +1]
        ofi_norm = math.tanh(ofi / 50.0)
        net_pressure = float(np.clip(0.6 * ofi_norm + 0.4 * book_imbalance, -1.0, 1.0))

        # Asymmetric Pressure Multipliers:
        # Bullish pressure (+net_pressure): Expand bid (strong support), contract ask (avoid toxic buy sweeps)
        # Bearish pressure (-net_pressure): Expand ask (strong selling), contract bid (avoid knife catching)
        if net_pressure >= 0:
            bid_pressure_mult = 1.0 + (0.35 * net_pressure)
            ask_pressure_mult = 1.0 - (0.50 * net_pressure)
        else:
            bid_pressure_mult = 1.0 + (0.50 * net_pressure)  # net_pressure is negative, so decreases
            ask_pressure_mult = 1.0 - (0.35 * net_pressure)  # net_pressure is negative, so increases

        # Calculate unconstrained notionals
        raw_bid_usd = base_size * profit_mult * bid_pressure_mult
        raw_ask_usd = base_size * profit_mult * ask_pressure_mult

        # Clamp between min_order_size_usd and max_order_size_usd
        bid_usd = float(np.clip(raw_bid_usd, min_order_size_usd, max_order_size_usd))
        ask_usd = float(np.clip(raw_ask_usd, min_order_size_usd, max_order_size_usd))

        # 3. Inventory Headroom Clamping
        # If long inventory is close to max, bid must not exceed remaining room
        room_long = max(0.0, max_inventory_usd - inventory_usd)
        if bid_usd > room_long:
            bid_usd = max(room_long, 0.0)

        # If short inventory is close to -max, ask must not exceed remaining short room
        room_short = max(0.0, max_inventory_usd + inventory_usd)
        if ask_usd > room_short:
            ask_usd = max(room_short, 0.0)

        bid_qty = round(bid_usd / mid_price, 4) if bid_usd >= min_order_size_usd else 0.0
        ask_qty = round(ask_usd / mid_price, 4) if ask_usd >= min_order_size_usd else 0.0

        return bid_qty, ask_qty, round(bid_usd, 2), round(ask_usd, 2)
