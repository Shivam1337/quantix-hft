"""
Live High-Frequency Trading Controller.
Manages market data ingestion from Hyperliquid, calculates Avellaneda-Stoikov + OFI quotes,
handles simulated paper fills (and modular live execution), and publishes real-time telemetry.
"""

import asyncio
import json
import time
import math
from typing import Optional, List, Dict, Any
import numpy as np

from signals import BookLevel, OrderBook, AvellanedaStoikovModel, RollingVolatility, calculate_ofi


class LiveHFTTrader:
    """
    Asynchronous trading state machine and execution manager.
    """

    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(self):
        self.status = "STOPPED"  # STOPPED, RUNNING
        self.mode = "SIMULATED"   # SIMULATED (future: LIVE)

        # Configurable Parameters
        self.coin = "PONS"
        self.order_size_usd = 50.0
        self.gamma = 0.5
        self.kappa = 1.5
        self.beta_ofi = 0.6
        self.min_spread_bps = 2.0
        self.max_inventory_usd = 250.0
        self.maker_fee_rate = 0.0001 # 0.01%
        self.latency_ms = 10.0

        # Financial State
        self.initial_capital = 1000.0
        self.cash = 1000.0
        self.inventory = 0.0
        self.total_fees = 0.0
        self.fills_count = 0

        # Market State
        self.mid_price = 0.0
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.spread_bps = 0.0
        self.current_ofi = 0.0
        self.volatility = 0.0005
        self.active_bid = 0.0
        self.active_ask = 0.0
        self.our_quote_size = 0.0

        # Queue / Latency Simulation
        self.pending_bid = 0.0
        self.pending_ask = 0.0
        self.pending_quote_time = 0.0

        # History & Telemetry
        self.recent_fills: List[Dict[str, Any]] = []
        self.equity_history: List[Dict[str, Any]] = []
        self.book_depth: Dict[str, List[Dict[str, Any]]] = {"bids": [], "asks": []}

        # Internal components
        self.vol_estimator = RollingVolatility(window_size=50)
        self.model: Optional[AvellanedaStoikovModel] = None
        self.current_book: Optional[OrderBook] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.last_update_time = time.time()

    def configure(self, config: Dict[str, Any]):
        """Updates strategy hyperparameters."""
        self.coin = config.get("coin", self.coin).upper()
        self.order_size_usd = float(config.get("order_size_usd", self.order_size_usd))
        self.gamma = float(config.get("gamma", self.gamma))
        self.beta_ofi = float(config.get("beta_ofi", self.beta_ofi))
        self.min_spread_bps = float(config.get("min_spread_bps", self.min_spread_bps))
        self.max_inventory_usd = float(config.get("max_inventory_usd", self.max_inventory_usd))
        self.mode = config.get("mode", self.mode)

        # Infer tick size based on price
        tick_sz = 0.0001
        if self.mid_price > 1000:
            tick_sz = 0.1
        elif self.mid_price > 10:
            tick_sz = 0.001
        elif self.mid_price > 1:
            tick_sz = 0.0001
        elif self.mid_price > 0:
            tick_sz = 0.00001

        self.model = AvellanedaStoikovModel(
            gamma=self.gamma,
            kappa=self.kappa,
            beta_ofi=self.beta_ofi,
            tick_size=tick_sz,
            min_spread_bps=self.min_spread_bps,
            max_inventory_usd=self.max_inventory_usd
        )

    def reset_account(self):
        """Resets paper capital and clears performance history."""
        self.cash = self.initial_capital
        self.inventory = 0.0
        self.total_fees = 0.0
        self.fills_count = 0
        self.recent_fills.clear()
        self.equity_history.clear()
        self.active_bid = 0.0
        self.active_ask = 0.0

    async def start(self, config: Optional[Dict[str, Any]] = None):
        """Starts the live market maker loop."""
        async with self._lock:
            if self.status == "RUNNING":
                return

            if config:
                self.configure(config)

            self.status = "RUNNING"
            self._task = asyncio.create_task(self._market_data_loop())

    async def stop(self):
        """Stops the market maker and cancels active quotes."""
        async with self._lock:
            self.status = "STOPPED"
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self.active_bid = 0.0
            self.active_ask = 0.0

    async def _market_data_loop(self):
        """Persistent WebSocket loop connecting to Hyperliquid."""
        import websockets
        retry_delay = 2.0

        while self.status == "RUNNING":
            try:
                async with websockets.connect(self.WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    retry_delay = 2.0
                    # Subscribe to L2 Book and Trades
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": self.coin}}))
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": self.coin}}))

                    while self.status == "RUNNING":
                        msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        data = json.loads(msg)
                        channel = data.get("channel")
                        payload = data.get("data")

                        if channel == "l2Book" and payload:
                            self._handle_book_update(payload)
                        elif channel == "trades" and payload:
                            self._handle_trades(payload)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Trader Error] {e}. Reconnecting in {retry_delay:.1f}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 10.0)

    def _handle_book_update(self, data: Dict[str, Any]):
        """Processes incoming L2 order book updates."""
        raw_bids = data.get("levels", [[], []])[0]
        raw_asks = data.get("levels", [[], []])[1]
        if not raw_bids or not raw_asks:
            return

        bids = [BookLevel(price=float(b["px"]), size=float(b["sz"]), orders=int(b.get("n", 1))) for b in raw_bids[:10]]
        asks = [BookLevel(price=float(a["px"]), size=float(a["sz"]), orders=int(a.get("n", 1))) for a in raw_asks[:10]]

        now = time.time()
        new_book = OrderBook(bids=bids, asks=asks, timestamp=now)
        mid = new_book.mid_price
        if mid <= 0:
            return

        self.mid_price = round(mid, 6)
        self.best_bid = round(new_book.best_bid, 6)
        self.best_ask = round(new_book.best_ask, 6)
        self.spread_bps = round(new_book.spread_bps, 2)

        # Store depth representation for UI
        self.book_depth = {
            "bids": [{"px": round(b.price, 6), "sz": round(b.size, 2)} for b in bids[:6]],
            "asks": [{"px": round(a.price, 6), "sz": round(a.size, 2)} for a in asks[:6]]
        }

        # Calculate Volatility & OFI
        self.volatility = self.vol_estimator.update(mid)
        ofi = 0.0
        if self.current_book is not None:
            ofi = calculate_ofi(self.current_book, new_book, levels=3)
        self.current_ofi = round(ofi, 2)
        self.current_book = new_book

        # Update model tick size if not set
        if not self.model:
            self.configure({})

        # Activate pending quote after simulated transit latency
        if self.pending_quote_time > 0 and now >= self.pending_quote_time:
            self.active_bid = self.pending_bid
            self.active_ask = self.pending_ask
            self.pending_quote_time = 0.0

        # Check circuit breakers
        current_inv_notional = abs(self.inventory * mid)
        can_bid = (current_inv_notional < self.max_inventory_usd) or (self.inventory < 0)
        can_ask = (current_inv_notional < self.max_inventory_usd) or (self.inventory > 0)

        # Generate quotes with AS+OFI model
        target_bid, target_ask = self.model.generate_quotes(
            book=new_book,
            inventory=self.inventory,
            volatility=self.volatility,
            ofi=ofi,
            use_inventory_skew=True,
            use_ofi=True
        )

        quote_qty = round(self.order_size_usd / mid, 4)
        self.our_quote_size = quote_qty

        # Stage new quote with simulated latency
        self.pending_bid = target_bid if can_bid else 0.0
        self.pending_ask = target_ask if can_ask else float('inf')
        self.pending_quote_time = now + (self.latency_ms / 1000.0)

        # Record equity progression (sample once every ~500ms)
        if now - self.last_update_time >= 0.5:
            self.last_update_time = now
            equity = self.cash + (self.inventory * mid)
            self.equity_history.append({
                "time": now,
                "equity": round(equity, 3),
                "inventory": round(self.inventory, 4),
                "mid": round(mid, 6)
            })
            if len(self.equity_history) > 150:
                self.equity_history.pop(0)

    def _handle_trades(self, trade_list: List[Dict[str, Any]]):
        """Matches incoming real-world market trades against our active quotes."""
        if not self.active_bid or not self.active_ask or self.active_bid <= 0:
            return

        now = time.time()
        for t in trade_list:
            px = float(t["px"])
            sz = float(t["sz"])
            side = t["side"] # 'B' = Taker Buy, 'A' = Taker Sell

            # Real market taker BUY: hits our passive ASK quote
            if side == "B" and px >= self.active_ask:
                fill_sz = min(self.our_quote_size, sz)
                fill_px = self.active_ask
                notional = fill_sz * fill_px
                fee = notional * self.maker_fee_rate

                self.cash += (notional - fee)
                self.inventory -= fill_sz
                self.total_fees += fee
                self.fills_count += 1

                fill_record = {
                    "id": self.fills_count,
                    "time": time.strftime("%H:%M:%S", time.localtime(now)),
                    "side": "SELL",
                    "price": round(fill_px, 6),
                    "size": round(fill_sz, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 4),
                    "inventory_after": round(self.inventory, 4)
                }
                self.recent_fills.insert(0, fill_record)
                if len(self.recent_fills) > 40:
                    self.recent_fills.pop()

                # Invalidate quote until next book update
                self.active_ask = float('inf')

            # Real market taker SELL: hits our passive BID quote
            elif side == "A" and px <= self.active_bid:
                fill_sz = min(self.our_quote_size, sz)
                fill_px = self.active_bid
                notional = fill_sz * fill_px
                fee = notional * self.maker_fee_rate

                self.cash -= (notional + fee)
                self.inventory += fill_sz
                self.total_fees += fee
                self.fills_count += 1

                fill_record = {
                    "id": self.fills_count,
                    "time": time.strftime("%H:%M:%S", time.localtime(now)),
                    "side": "BUY",
                    "price": round(fill_px, 6),
                    "size": round(fill_sz, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 4),
                    "inventory_after": round(self.inventory, 4)
                }
                self.recent_fills.insert(0, fill_record)
                if len(self.recent_fills) > 40:
                    self.recent_fills.pop()

                # Invalidate quote until next book update
                self.active_bid = 0.0

    def get_telemetry(self) -> Dict[str, Any]:
        """Serializes current state for WebSockets & UI."""
        mid = self.mid_price if self.mid_price > 0 else 1.0
        equity = self.cash + (self.inventory * mid)
        net_pnl = equity - self.initial_capital
        return_pct = (net_pnl / self.initial_capital) * 100.0

        return {
            "status": self.status,
            "mode": self.mode,
            "coin": self.coin,
            "equity": round(equity, 2),
            "cash": round(self.cash, 2),
            "inventory": round(self.inventory, 4),
            "inventory_usd": round(self.inventory * mid, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round(return_pct, 3),
            "mid_price": self.mid_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread_bps": self.spread_bps,
            "active_bid": self.active_bid if self.active_bid > 0 else None,
            "active_ask": self.active_ask if self.active_ask < float('inf') else None,
            "ofi": self.current_ofi,
            "volatility": round(self.volatility * 10000, 2), # in bps
            "fills_count": self.fills_count,
            "total_fees": round(self.total_fees, 4),
            "book_depth": self.book_depth,
            "recent_fills": self.recent_fills[:15],
            "equity_history": self.equity_history[-50:],
            "config": {
                "coin": self.coin,
                "order_size_usd": self.order_size_usd,
                "gamma": self.gamma,
                "beta_ofi": self.beta_ofi,
                "min_spread_bps": self.min_spread_bps,
                "max_inventory_usd": self.max_inventory_usd
            }
        }
