"""
Live High-Frequency Trading Controller.
Manages market data ingestion from Hyperliquid, calculates Avellaneda-Stoikov + OFI quotes,
handles simulated paper fills (and modular live execution), and publishes real-time telemetry.
"""

import asyncio
import json
import time
import math
from typing import Optional, List, Dict, Any, Tuple
import numpy as np

from signals import BookLevel, OrderBook, AvellanedaStoikovModel, RollingVolatility, calculate_ofi
from database import db


class LiveHFTTrader:
    """
    Asynchronous trading state machine and execution manager.
    """

    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(self):
        self.status = "STOPPED"  # STOPPED, RUNNING
        self.mode = "SIMULATED"   # SIMULATED (future: LIVE)
        self.session_id: Optional[int] = None

        # Configurable Parameters
        self.coin = "PONS"
        self.order_size_usd = 50.0
        self.gamma = 0.6
        self.kappa = 1.5
        self.beta_ofi = 0.7
        self.min_spread_bps = 2.0
        self.min_market_spread_bps = 4.5  # Gatekeeper: only quote when book spread >= 4.5 bps
        self.max_inventory_usd = 200.0
        self.maker_fee_rate = 0.00015     # 0.015% Hyperliquid real base maker fee
        self.taker_fee_rate = 0.00045     # 0.045% Hyperliquid real base taker fee
        self.latency_ms = 10.0

        # Financial State
        self.initial_capital = 1000.0
        self.cash = 1000.0
        self.inventory = 0.0
        self.entry_price = 0.0            # Weighted average entry price of inventory
        self.total_fees = 0.0
        self.fills_count = 0

        # Momentum & Protection State
        self.price_history: List[Tuple[float, float]] = [] # (time, mid_price)
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.last_circuit_break_time = 0.0

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
        self.min_market_spread_bps = float(config.get("min_market_spread_bps", self.min_market_spread_bps))
        self.max_inventory_usd = float(config.get("max_inventory_usd", self.max_inventory_usd))
        self.maker_fee_rate = float(config.get("maker_fee_rate", self.maker_fee_rate))
        self.taker_fee_rate = float(config.get("taker_fee_rate", self.taker_fee_rate))
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
        self.entry_price = 0.0
        self.total_fees = 0.0
        self.fills_count = 0
        self.price_history.clear()
        self.circuit_breaker_active = False
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
            # Persist new trading session in DB
            self.session_id = await db.create_session(
                coin=self.coin,
                initial_capital=self.initial_capital,
                config=self.get_telemetry().get("config", {})
            )
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

            # Finalize session in DB
            if self.session_id:
                mid = self.mid_price if self.mid_price > 0 else 1.0
                final_eq = self.cash + (self.inventory * mid)
                await db.end_session(
                    session_id=self.session_id,
                    final_equity=final_eq,
                    net_pnl=final_eq - self.initial_capital,
                    total_fills=self.fills_count
                )

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

        # 1. Minimum Market Spread Gatekeeper:
        # Never quote when spread < min_market_spread_bps because round-trip fees (3 bps) exceed edge
        if new_book.spread_bps < self.min_market_spread_bps:
            self.active_bid = 0.0
            self.active_ask = float('inf')
            self.pending_bid = 0.0
            self.pending_ask = float('inf')
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = f"Spread too tight ({new_book.spread_bps:.2f} < {self.min_market_spread_bps:.1f} bps)"
            return

        # 2. Track 10-second Momentum
        self.price_history.append((now, mid))
        self.price_history = [p for p in self.price_history if now - p[0] <= 10.0]
        momentum_bps = 0.0
        if len(self.price_history) >= 2:
            oldest_px = self.price_history[0][1]
            momentum_bps = (mid - oldest_px) / oldest_px * 10000.0

        dumping = (momentum_bps < -10.0) or (ofi < -1200.0)
        pumping = (momentum_bps > +10.0) or (ofi > +1200.0)

        # 3. Emergency Taker Stop-Loss:
        # If inventory drawdown exceeds 30 bps from entry price, execute immediate taker liquidation
        inv_usd = self.inventory * mid
        abs_inv = abs(inv_usd)
        if self.entry_price > 0 and abs_inv > 30.0:
            price_delta_bps = (mid - self.entry_price) / self.entry_price * 10000.0
            unrealized_loss_bps = -price_delta_bps if self.inventory > 0 else price_delta_bps
            if unrealized_loss_bps > 25.0:  # 25 bps stop-loss
                # Emergency close via taker order
                exit_px = new_book.best_bid if self.inventory > 0 else new_book.best_ask
                notional = abs_inv
                fee = notional * self.taker_fee_rate
                self.cash += (self.inventory * exit_px) - fee
                self.total_fees += fee
                self.fills_count += 1
                asyncio.create_task(db.log_fill(
                    session_id=self.session_id,
                    coin=self.coin,
                    side="STOP_LOSS_SELL" if self.inventory > 0 else "STOP_LOSS_BUY",
                    price=exit_px,
                    size=abs(self.inventory),
                    notional=notional,
                    fee=fee,
                    fee_type="TAKER",
                    inventory_after=0.0,
                    cash_after=self.cash
                ))
                self.recent_fills.insert(0, {
                    "id": self.fills_count,
                    "time": time.strftime("%H:%M:%S", time.localtime(now)),
                    "side": "STOP_LOSS_SELL" if self.inventory > 0 else "STOP_LOSS_BUY",
                    "price": round(exit_px, 6),
                    "size": round(abs(self.inventory), 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 4),
                    "inventory_after": 0.0
                })
                self.inventory = 0.0
                self.entry_price = 0.0
                self.active_bid = 0.0
                self.active_ask = float('inf')
                self.last_circuit_break_time = now + 10.0 # Freeze for 10s
                return

        # Check circuit breaker timeout
        if now < self.last_circuit_break_time:
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = "Cooling down after stop-loss"
            self.active_bid = 0.0
            self.active_ask = float('inf')
            return

        # 4. Unilateral Inventory Offloading:
        # If holding long inventory ($ > 30), shut off buying completely!
        # If holding short inventory ($ < -30), shut off selling completely!
        can_bid = True
        can_ask = True

        if inv_usd > 30.0:
            can_bid = False  # DO NOT BUY
            can_ask = True   # AGGRESSIVELY SELL
        elif inv_usd < -30.0:
            can_ask = False  # DO NOT SELL
            can_bid = True   # AGGRESSIVELY BUY

        if abs_inv >= self.max_inventory_usd:
            can_bid = (self.inventory < 0)
            can_ask = (self.inventory > 0)

        # Freeze quotes against the trend
        if dumping:
            can_bid = False  # Never buy into a falling knife
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = f"Sell waterfall ({momentum_bps:.1f} bps / OFI {ofi:.0f})"
        elif pumping:
            can_ask = False  # Never short into a violent pump
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = f"Buy surge (+{momentum_bps:.1f} bps / OFI {ofi:.0f})"
        else:
            self.circuit_breaker_active = False
            self.circuit_breaker_reason = ""

        # Activate pending quote after simulated transit latency
        if self.pending_quote_time > 0 and now >= self.pending_quote_time:
            self.active_bid = self.pending_bid
            self.active_ask = self.pending_ask
            self.pending_quote_time = 0.0

        # Generate quotes with AS+OFI model
        target_bid, target_ask = self.model.generate_quotes(
            book=new_book,
            inventory=self.inventory,
            volatility=self.volatility,
            ofi=ofi,
            use_inventory_skew=True,
            use_ofi=True
        )

        # If holding inventory, quote the exit side aggressively at the top of book to dump fast
        if inv_usd > 30.0 and new_book.best_ask > 0:
            target_ask = min(target_ask, new_book.best_ask)
        elif inv_usd < -30.0 and new_book.best_bid > 0:
            target_bid = max(target_bid, new_book.best_bid)

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

            # Log periodic telemetry to PostgreSQL
            if self.session_id:
                asyncio.create_task(db.log_telemetry(
                    session_id=self.session_id,
                    coin=self.coin,
                    mid_price=mid,
                    spread_bps=new_book.spread_bps,
                    ofi=ofi,
                    volatility_bps=self.volatility * 10000.0,
                    equity=equity,
                    inventory=self.inventory,
                    circuit_breaker_active=self.circuit_breaker_active,
                    circuit_breaker_reason=self.circuit_breaker_reason
                ))

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

                # Update entry price
                if self.inventory < 0:
                    self.entry_price = ((self.entry_price * abs(self.inventory)) + (fill_px * fill_sz)) / (abs(self.inventory) + fill_sz)
                elif abs(self.inventory - fill_sz) < 1e-4:
                    self.entry_price = 0.0
                else:
                    self.entry_price = fill_px

                self.cash += (notional - fee)
                self.inventory -= fill_sz
                if abs(self.inventory) < 1e-4:
                    self.inventory = 0.0
                    self.entry_price = 0.0

                self.total_fees += fee
                self.fills_count += 1

                asyncio.create_task(db.log_fill(
                    session_id=self.session_id,
                    coin=self.coin,
                    side="SELL",
                    price=fill_px,
                    size=fill_sz,
                    notional=notional,
                    fee=fee,
                    fee_type="MAKER",
                    inventory_after=self.inventory,
                    cash_after=self.cash
                ))

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

                # Update entry price
                if self.inventory > 0:
                    self.entry_price = ((self.entry_price * self.inventory) + (fill_px * fill_sz)) / (self.inventory + fill_sz)
                elif abs(self.inventory + fill_sz) < 1e-4:
                    self.entry_price = 0.0
                else:
                    self.entry_price = fill_px

                self.cash -= (notional + fee)
                self.inventory += fill_sz
                if abs(self.inventory) < 1e-4:
                    self.inventory = 0.0
                    self.entry_price = 0.0

                self.total_fees += fee
                self.fills_count += 1

                asyncio.create_task(db.log_fill(
                    session_id=self.session_id,
                    coin=self.coin,
                    side="BUY",
                    price=fill_px,
                    size=fill_sz,
                    notional=notional,
                    fee=fee,
                    fee_type="MAKER",
                    inventory_after=self.inventory,
                    cash_after=self.cash
                ))

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
            "circuit_breaker_active": self.circuit_breaker_active,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "maker_fee_rate": self.maker_fee_rate,
            "taker_fee_rate": self.taker_fee_rate,
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
                "min_market_spread_bps": self.min_market_spread_bps,
                "max_inventory_usd": self.max_inventory_usd,
                "maker_fee_bps": round(self.maker_fee_rate * 10000, 1),
                "taker_fee_bps": round(self.taker_fee_rate * 10000, 1)
            }
        }
