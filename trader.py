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

        # Configurable Parameters ($50 Capital & $10 Sizing Scale)
        self.coin = "PONS"
        self.order_size_usd = 10.0        # Default max order size $10
        self.min_order_size_usd = 3.0     # Minimum order size floor
        self.dynamic_sizing = True        # Order book pressure & profit adaptive sizing
        self.gamma = 0.6
        self.kappa = 1.5
        self.beta_ofi = 0.7
        self.min_spread_bps = 2.0
        self.min_market_spread_bps = 4.5  # Gatekeeper: only quote when book spread >= 4.5 bps
        self.max_inventory_usd = 25.0     # Max inventory $25 for $50 capital
        self.maker_fee_rate = 0.00015     # 0.015% Hyperliquid real base maker fee
        self.taker_fee_rate = 0.00045     # 0.045% Hyperliquid real base taker fee
        self.latency_ms = 10.0

        # Dynamic 15-Minute Pair Rotation Engine
        self.auto_rotate = True
        self.rotation_interval_sec = 900.0   # 15 mins base rotation window
        self.min_pair_duration_sec = 600.0   # 10 mins minimum duration before evaluating rotation
        self.max_pair_duration_sec = 1800.0  # 30 mins hard ceiling
        self.trades_target_per_pair = 12     # Series of trades threshold
        self.pair_start_time = time.time()
        self.pair_start_equity = 50.0
        self.pair_fills_count = 0
        self.pair_status = "ACTIVE"          # "ACTIVE", "FLATTENING", "SWITCHING"
        self.flattening_start_time = 0.0
        self.rotation_reason = ""
        self.rotation_history: List[Dict[str, Any]] = []
        self._ws: Any = None
        self._switching_lock = asyncio.Lock()

        # Financial State ($50 Starting Capital)
        self.initial_capital = 50.0
        self.cash = 50.0
        self.inventory = 0.0
        self.entry_price = 0.0            # Weighted average entry price of inventory
        self.total_fees = 0.0
        self.fills_count = 0

        # Momentum & Protection State
        self.price_history: List[Tuple[float, float]] = [] # (time, mid_price)
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.last_circuit_break_time = 0.0

        # Market State & Asymmetric Sizing
        self.mid_price = 0.0
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.spread_bps = 0.0
        self.current_ofi = 0.0
        self.volatility = 0.0005
        self.active_bid = 0.0
        self.active_ask = 0.0
        self.our_quote_size = 0.0
        self.our_bid_size = 0.0
        self.our_ask_size = 0.0
        self.our_bid_usd = 10.0
        self.our_ask_usd = 10.0

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
        if "initial_capital" in config:
            self.initial_capital = float(config["initial_capital"])
            if self.status == "STOPPED" and self.fills_count == 0:
                self.cash = self.initial_capital
                self.pair_start_equity = self.initial_capital
        self.order_size_usd = float(config.get("order_size_usd", self.order_size_usd))
        if "min_order_size_usd" in config:
            self.min_order_size_usd = float(config["min_order_size_usd"])
        if "dynamic_sizing" in config:
            self.dynamic_sizing = bool(config["dynamic_sizing"])
        self.gamma = float(config.get("gamma", self.gamma))
        self.beta_ofi = float(config.get("beta_ofi", self.beta_ofi))
        self.min_spread_bps = float(config.get("min_spread_bps", self.min_spread_bps))
        self.min_market_spread_bps = float(config.get("min_market_spread_bps", self.min_market_spread_bps))
        self.max_inventory_usd = float(config.get("max_inventory_usd", self.max_inventory_usd))
        self.maker_fee_rate = float(config.get("maker_fee_rate", self.maker_fee_rate))
        self.taker_fee_rate = float(config.get("taker_fee_rate", self.taker_fee_rate))
        self.mode = config.get("mode", self.mode)

        if "auto_rotate" in config:
            self.auto_rotate = bool(config["auto_rotate"])
        if "rotation_interval_min" in config:
            self.rotation_interval_sec = float(config["rotation_interval_min"]) * 60.0
        if "trades_target_per_pair" in config:
            self.trades_target_per_pair = int(config["trades_target_per_pair"])

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
        """Resets paper capital to $50.00 and clears performance history."""
        self.cash = self.initial_capital
        self.inventory = 0.0
        self.entry_price = 0.0
        self.total_fees = 0.0
        self.fills_count = 0
        self.price_history.clear()
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.recent_fills.clear()
        self.equity_history.clear()
        self.active_bid = 0.0
        self.active_ask = 0.0
        self.our_quote_size = 0.0
        self.our_bid_size = 0.0
        self.our_ask_size = 0.0
        self.our_bid_usd = self.order_size_usd
        self.our_ask_usd = self.order_size_usd
        self.pending_bid = 0.0
        self.pending_ask = 0.0
        self.pair_start_time = time.time()
        self.pair_start_equity = self.cash
        self.pair_fills_count = 0
        self.pair_status = "ACTIVE"
        self.rotation_reason = ""
        self.rotation_history.clear()

    async def find_best_pair(self, exclude_coin: Optional[str] = None) -> Optional[str]:
        """Finds the best trading pair on Hyperliquid with high volume and widest spread."""
        import requests
        try:
            def _fetch_candidates():
                r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=5).json()
                universe = r[0]["universe"]
                ctxs = r[1]
                candidates = []
                for u, c in zip(universe, ctxs):
                    name = u["name"]
                    vol = float(c.get("dayNtlVlm", 0))
                    mark = float(c.get("markPx", 0))
                    if vol > 300000 and mark > 0:
                        candidates.append({"name": name, "vol": vol, "mark": mark})

                candidates.sort(key=lambda x: x["vol"], reverse=True)
                sample = candidates[:15] + candidates[-25:]

                results = []
                for c in sample:
                    try:
                        book = requests.post("https://api.hyperliquid.xyz/info", json={"type": "l2Book", "coin": c["name"]}, timeout=2).json()
                        bids = book.get("levels", [[], []])[0]
                        asks = book.get("levels", [[], []])[1]
                        if bids and asks:
                            bb = float(bids[0]["px"])
                            ba = float(asks[0]["px"])
                            spread_bps = (ba - bb) / bb * 10000.0
                            top_depth = float(bids[0]["sz"]) * bb
                            if spread_bps >= self.min_market_spread_bps:
                                results.append({
                                    "name": c["name"],
                                    "spread_bps": spread_bps,
                                    "vol": c["vol"],
                                    "top_depth": top_depth
                                })
                    except Exception:
                        pass

                results.sort(key=lambda x: x["spread_bps"], reverse=True)
                return results

            results = await asyncio.to_thread(_fetch_candidates)
            if not results:
                return None

            if exclude_coin:
                alternatives = [r for r in results if r["name"] != exclude_coin]
                if alternatives:
                    return alternatives[0]["name"]

            return results[0]["name"]
        except Exception as e:
            print(f"[Pair Hunter Error] Failed to scan pairs: {e}")
            return None

    def trigger_rotation(self, reason: str):
        """Initiates graceful position offload and scheduled pair rotation."""
        if self.pair_status in ("FLATTENING", "SWITCHING"):
            return
        self.pair_status = "FLATTENING"
        self.flattening_start_time = time.time()
        self.rotation_reason = reason
        self.active_bid = 0.0
        self.active_ask = float('inf')
        self.pending_bid = 0.0
        self.pending_ask = float('inf')
        print(f"[Pair Rotation] Initiated offload for {self.coin}: {reason}")
        if abs(self.inventory) < 1e-4:
            asyncio.create_task(self._execute_coin_switch())

    async def force_rotate(self, reason: str = "Manual User Rotation"):
        """Forces an immediate offload and pair rotation."""
        self.trigger_rotation(reason)

    async def _execute_coin_switch(self):
        """Cleanly rotates to the best scanned pair once inventory is strictly flat."""
        async with self._switching_lock:
            if self.pair_status == "SWITCHING":
                return
            self.pair_status = "SWITCHING"
            now = time.time()

            # Ensure inventory is strictly flat before moving on
            if abs(self.inventory) >= 1e-4:
                print(f"[Pair Rotation Warning] Residual inventory {self.inventory} detected before switch. Forcing flat.")
                self.inventory = 0.0
                self.entry_price = 0.0

            old_coin = self.coin
            duration = now - self.pair_start_time
            current_eq = self.cash
            pair_pnl = current_eq - self.pair_start_equity
            pair_return_pct = (pair_pnl / max(self.pair_start_equity, 1.0)) * 100.0
            fills_in_pair = self.pair_fills_count

            # Log rotation in DB
            asyncio.create_task(db.log_rotation(
                session_id=self.session_id,
                from_coin=old_coin,
                to_coin="SCANNING...",
                duration_sec=round(duration, 1),
                pair_pnl=round(pair_pnl, 4),
                pair_return_pct=round(pair_return_pct, 2),
                fills_count=fills_in_pair,
                reason=self.rotation_reason or "Dynamic Rotation"
            ))

            # Record in-memory rotation history
            self.rotation_history.insert(0, {
                "time": time.strftime("%H:%M:%S", time.localtime(now)),
                "from_coin": old_coin,
                "to_coin": "SCANNING...",
                "duration_min": round(duration / 60.0, 1),
                "pnl": round(pair_pnl, 2),
                "return_pct": round(pair_return_pct, 2),
                "fills": fills_in_pair,
                "reason": self.rotation_reason or "Dynamic Rotation"
            })

            # Search for best pair
            best_coin = await self.find_best_pair(exclude_coin=old_coin)
            if not best_coin:
                defaults = ["PONS", "CASHCAT", "PURR", "AZTEC", "GRAM"]
                best_coin = next((c for c in defaults if c != old_coin), old_coin)

            print(f"[Pair Rotation] Switching from {old_coin} -> {best_coin} (Past PnL: ${pair_pnl:+.2f})")
            if self.rotation_history:
                self.rotation_history[0]["to_coin"] = best_coin

            # Switch active coin
            new_coin = best_coin
            old_coin_val = self.coin
            self.coin = new_coin

            # Resubscribe WebSocket feeds
            if self._ws:
                try:
                    await self._ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "l2Book", "coin": old_coin_val}}))
                    await self._ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "trades", "coin": old_coin_val}}))
                    await self._ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": new_coin}}))
                    await self._ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": new_coin}}))
                except Exception as e:
                    print(f"[Pair Rotation] WebSocket resubscribe error: {e}")

            # Reset pair-level state
            self.pair_start_time = time.time()
            self.pair_start_equity = self.cash
            self.pair_fills_count = 0
            self.vol_estimator = RollingVolatility(window_size=50)
            self.current_book = None
            self.price_history.clear()
            self.book_depth = {"bids": [], "asks": []}
            self.active_bid = 0.0
            self.active_ask = 0.0
            self.pending_bid = 0.0
            self.pending_ask = 0.0
            self.circuit_breaker_active = False
            self.circuit_breaker_reason = ""
            self.rotation_reason = ""
            self.pair_status = "ACTIVE"

    async def start(self, config: Optional[Dict[str, Any]] = None):
        """Starts the live market maker loop."""
        async with self._lock:
            if self.status == "RUNNING":
                return

            if config:
                self.configure(config)

            self.status = "RUNNING"
            self.pair_start_time = time.time()
            self.pair_start_equity = self.cash
            self.pair_fills_count = 0
            self.pair_status = "ACTIVE"

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
            self.pair_status = "ACTIVE"

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
                    self._ws = ws
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
            finally:
                self._ws = None

    def _handle_book_update(self, data: Dict[str, Any]):
        """Processes incoming L2 order book updates."""
        if data.get("coin") and data.get("coin") != self.coin:
            return  # Discard updates from previous coin during switch

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

        inv_usd = self.inventory * mid
        abs_inv_usd = abs(inv_usd)

        # =========================================================================
        # 1. FLATTENING & 100% OFF-LOAD BEFORE ROTATION
        # =========================================================================
        if self.pair_status == "FLATTENING":
            abs_sz = abs(self.inventory)
            if abs_sz < 1e-4 or abs_inv_usd < 0.1:
                self.inventory = 0.0
                self.entry_price = 0.0
                asyncio.create_task(self._execute_coin_switch())
                return

            # If waiting for passive fill takes > 15 seconds, execute immediate taker liquidation
            if (now - self.flattening_start_time) > 15.0:
                exit_px = new_book.best_bid if self.inventory > 0 else new_book.best_ask
                notional = abs_sz * exit_px
                fee = notional * self.taker_fee_rate
                self.cash += (self.inventory * exit_px) - fee
                self.total_fees += fee
                self.fills_count += 1
                self.pair_fills_count += 1

                asyncio.create_task(db.log_fill(
                    session_id=self.session_id,
                    coin=self.coin,
                    side="OFFLOAD_SELL" if self.inventory > 0 else "OFFLOAD_BUY",
                    price=exit_px,
                    size=abs_sz,
                    notional=notional,
                    fee=fee,
                    fee_type="TAKER",
                    inventory_after=0.0,
                    cash_after=self.cash
                ))
                self.recent_fills.insert(0, {
                    "id": self.fills_count,
                    "time": time.strftime("%H:%M:%S", time.localtime(now)),
                    "side": "OFFLOAD_SELL" if self.inventory > 0 else "OFFLOAD_BUY",
                    "price": round(exit_px, 6),
                    "size": round(abs_sz, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 4),
                    "inventory_after": 0.0
                })
                self.inventory = 0.0
                self.entry_price = 0.0
                print(f"[Pair Rotation] Taker forced 100% offload of {self.coin} before switch.")
                asyncio.create_task(self._execute_coin_switch())
                return

            # Passive aggressive offload quoting at top of book
            if self.inventory > 0:
                self.active_bid = 0.0
                self.active_ask = new_book.best_ask
                self.pending_bid = 0.0
                self.pending_ask = new_book.best_ask
            else:
                self.active_ask = float('inf')
                self.active_bid = new_book.best_bid
                self.pending_ask = float('inf')
                self.pending_bid = new_book.best_bid
            self.our_quote_size = round(abs_sz, 4)
            return

        if self.pair_status == "SWITCHING":
            self.active_bid = 0.0
            self.active_ask = float('inf')
            return

        # =========================================================================
        # 2. DYNAMIC LIFECYCLE & PAIR ROTATION LOGIC
        # =========================================================================
        if self.auto_rotate and self.pair_status == "ACTIVE":
            pair_duration = now - self.pair_start_time
            pair_equity = self.cash + inv_usd
            pair_pnl = pair_equity - self.pair_start_equity
            is_profitable = pair_pnl > 0.02
            has_edge = (new_book.spread_bps >= self.min_market_spread_bps) and (not self.circuit_breaker_active)
            series_done = self.pair_fills_count >= self.trades_target_per_pair

            # Rule A: Stagnant liquidity (0 fills in 10 minutes) -> rotate to active pair
            if pair_duration >= 600.0 and self.pair_fills_count == 0:
                self.trigger_rotation("Stagnant orderbook (0 fills in 10m)")
                return

            # Rule B: Hard ceiling (30 mins) -> lock in gains and rotate
            if pair_duration >= self.max_pair_duration_sec:
                self.trigger_rotation(f"30-min window reached (PnL: ${pair_pnl:+.2f})")
                return

            # Rule C: Dynamic 15-minute rotation window
            if pair_duration >= self.rotation_interval_sec:
                # If currently profitable and order book still has wide spread/edge, dynamically stay!
                if is_profitable and has_edge and (pair_duration < self.max_pair_duration_sec):
                    pass # Keep riding the winning coin
                else:
                    self.trigger_rotation(f"15-min cycle reached (PnL: ${pair_pnl:+.2f}, edge tapering)")
                    return

            # Rule D: Series of trades completed & past 10m minimum, edge depleted or plateaued
            if pair_duration >= self.min_pair_duration_sec and series_done and (not has_edge or not is_profitable):
                self.trigger_rotation(f"Series of {self.pair_fills_count} trades done (no edge remaining)")
                return

        # =========================================================================
        # 3. SAFETY CHECKS & CIRCUIT BREAKERS
        # =========================================================================
        # Gatekeeper: Never quote when book spread < min_market_spread_bps
        if new_book.spread_bps < self.min_market_spread_bps:
            self.active_bid = 0.0
            self.active_ask = float('inf')
            self.pending_bid = 0.0
            self.pending_ask = float('inf')
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = f"Spread too tight ({new_book.spread_bps:.2f} < {self.min_market_spread_bps:.1f} bps)"
            return

        # Momentum tracking (10-second window)
        self.price_history.append((now, mid))
        self.price_history = [p for p in self.price_history if now - p[0] <= 10.0]
        momentum_bps = 0.0
        if len(self.price_history) >= 2:
            oldest_px = self.price_history[0][1]
            momentum_bps = (mid - oldest_px) / oldest_px * 10000.0

        dumping = (momentum_bps < -10.0) or (ofi < -1200.0)
        pumping = (momentum_bps > +10.0) or (ofi > +1200.0)

        # Dynamic Emergency Taker Stop-Loss:
        # Scale with order size (check on positions >= $7.00)
        stop_loss_trigger_usd = max(self.order_size_usd * 0.7, 5.0)
        if self.entry_price > 0 and abs_inv_usd >= stop_loss_trigger_usd:
            price_delta_bps = (mid - self.entry_price) / self.entry_price * 10000.0
            unrealized_loss_bps = -price_delta_bps if self.inventory > 0 else price_delta_bps
            if unrealized_loss_bps > 25.0:  # 25 bps stop-loss
                exit_px = new_book.best_bid if self.inventory > 0 else new_book.best_ask
                notional = abs_inv_usd
                fee = notional * self.taker_fee_rate
                self.cash += (self.inventory * exit_px) - fee
                self.total_fees += fee
                self.fills_count += 1
                self.pair_fills_count += 1
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

        # Check circuit breaker cooldown
        if now < self.last_circuit_break_time:
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = "Cooling down after stop-loss"
            self.active_bid = 0.0
            self.active_ask = float('inf')
            return

        # Dynamic Unilateral Inventory Offloading:
        # Scale with order size (e.g. 1.5x order size = $15)
        offload_threshold_usd = max(self.order_size_usd * 1.5, 12.0)
        can_bid = True
        can_ask = True

        if inv_usd >= offload_threshold_usd:
            can_bid = False  # DO NOT BUY MORE
            can_ask = True   # AGGRESSIVELY SELL
        elif inv_usd <= -offload_threshold_usd:
            can_ask = False  # DO NOT SELL MORE
            can_bid = True   # AGGRESSIVELY BUY

        if abs_inv_usd >= self.max_inventory_usd:
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

        # =========================================================================
        # 4. QUOTE GENERATION & DYNAMIC SIZING
        # =========================================================================
        target_bid, target_ask = self.model.generate_quotes(
            book=new_book,
            inventory=self.inventory,
            volatility=self.volatility,
            ofi=ofi,
            use_inventory_skew=True,
            use_ofi=True
        )

        # Calculate top-of-book depth imbalance in [-1, +1]
        top_bid_sz = new_book.best_bid_size * new_book.best_bid
        top_ask_sz = new_book.best_ask_size * new_book.best_ask
        total_depth = top_bid_sz + top_ask_sz
        book_imbalance = (top_bid_sz - top_ask_sz) / total_depth if total_depth > 0 else 0.0

        current_pair_pnl = (self.cash + inv_usd) - self.pair_start_equity

        # Dynamic Quote Sizing based on Order Book Pressure & Profit Momentum
        if self.dynamic_sizing:
            bid_qty, ask_qty, bid_usd, ask_usd = self.model.compute_dynamic_sizes(
                mid_price=mid,
                max_order_size_usd=self.order_size_usd,
                min_order_size_usd=self.min_order_size_usd,
                inventory_usd=inv_usd,
                max_inventory_usd=self.max_inventory_usd,
                ofi=ofi,
                book_imbalance=book_imbalance,
                pair_pnl=current_pair_pnl
            )
            self.our_bid_size = bid_qty
            self.our_ask_size = ask_qty
            self.our_bid_usd = bid_usd
            self.our_ask_usd = ask_usd
            self.our_quote_size = round((bid_qty + ask_qty) / 2.0, 4)
        else:
            static_qty = round(self.order_size_usd / mid, 4)
            self.our_bid_size = static_qty
            self.our_ask_size = static_qty
            self.our_bid_usd = self.order_size_usd
            self.our_ask_usd = self.order_size_usd
            self.our_quote_size = static_qty

        # If holding inventory, quote the exit side aggressively at the top of book to dump fast
        if inv_usd >= offload_threshold_usd and new_book.best_ask > 0:
            target_ask = min(target_ask, new_book.best_ask)
            self.our_ask_size = round(abs(self.inventory), 4)
            self.our_ask_usd = round(abs_inv_usd, 2)
        elif inv_usd <= -offload_threshold_usd and new_book.best_bid > 0:
            target_bid = max(target_bid, new_book.best_bid)
            self.our_bid_size = round(abs(self.inventory), 4)
            self.our_bid_usd = round(abs_inv_usd, 2)

        # Stage new quote with simulated latency
        self.pending_bid = target_bid if (can_bid and self.our_bid_size > 0) else 0.0
        self.pending_ask = target_ask if (can_ask and self.our_ask_size > 0) else float('inf')
        self.pending_quote_time = now + (self.latency_ms / 1000.0)

        # Record equity progression (sample once every ~500ms)
        if now - self.last_update_time >= 0.5:
            self.last_update_time = now
            equity = self.cash + inv_usd
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

        if trade_list and trade_list[0].get("coin") and trade_list[0].get("coin") != self.coin:
            return

        now = time.time()
        for t in trade_list:
            px = float(t["px"])
            sz = float(t["sz"])
            side = t["side"] # 'B' = Taker Buy, 'A' = Taker Sell

            # Real market taker BUY: hits our passive ASK quote
            if side == "B" and px >= self.active_ask:
                ask_limit_sz = self.our_ask_size if self.our_ask_size > 0 else self.our_quote_size
                fill_sz = min(ask_limit_sz, sz)
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
                self.pair_fills_count += 1

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

                # If flattening and reached 0 inventory, trigger immediate switch
                if self.pair_status == "FLATTENING" and abs(self.inventory) < 1e-4:
                    asyncio.create_task(self._execute_coin_switch())

            # Real market taker SELL: hits our passive BID quote
            elif side == "A" and px <= self.active_bid:
                bid_limit_sz = self.our_bid_size if self.our_bid_size > 0 else self.our_quote_size
                fill_sz = min(bid_limit_sz, sz)
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
                self.pair_fills_count += 1

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

                # If flattening and reached 0 inventory, trigger immediate switch
                if self.pair_status == "FLATTENING" and abs(self.inventory) < 1e-4:
                    asyncio.create_task(self._execute_coin_switch())

    def get_telemetry(self) -> Dict[str, Any]:
        """Serializes current state for WebSockets & UI."""
        mid = self.mid_price if self.mid_price > 0 else 1.0
        equity = self.cash + (self.inventory * mid)
        net_pnl = equity - self.initial_capital
        return_pct = (net_pnl / max(self.initial_capital, 1.0)) * 100.0

        pair_duration = time.time() - self.pair_start_time
        pair_pnl = equity - self.pair_start_equity
        pair_return_pct = (pair_pnl / max(self.pair_start_equity, 1.0)) * 100.0
        countdown = max(0, int(self.rotation_interval_sec - pair_duration))

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

            # Dynamic 15-Min Pair Rotation Telemetry
            "auto_rotate": self.auto_rotate,
            "pair_status": self.pair_status,
            "pair_duration_sec": round(pair_duration, 1),
            "pair_duration_min": round(pair_duration / 60.0, 1),
            "pair_pnl": round(pair_pnl, 2),
            "pair_return_pct": round(pair_return_pct, 2),
            "pair_fills_count": self.pair_fills_count,
            "rotation_countdown_sec": countdown,
            "rotation_countdown_str": f"{countdown // 60:02d}:{countdown % 60:02d}",
            "rotation_reason": self.rotation_reason,
            "rotation_history": self.rotation_history[:10],

            # Dynamic Sizing Telemetry
            "dynamic_sizing": self.dynamic_sizing,
            "our_bid_usd": self.our_bid_usd,
            "our_ask_usd": self.our_ask_usd,
            "our_bid_size": self.our_bid_size,
            "our_ask_size": self.our_ask_size,
            "min_order_size_usd": self.min_order_size_usd,

            "config": {
                "coin": self.coin,
                "initial_capital": self.initial_capital,
                "order_size_usd": self.order_size_usd,
                "min_order_size_usd": self.min_order_size_usd,
                "dynamic_sizing": self.dynamic_sizing,
                "gamma": self.gamma,
                "beta_ofi": self.beta_ofi,
                "min_spread_bps": self.min_spread_bps,
                "min_market_spread_bps": self.min_market_spread_bps,
                "max_inventory_usd": self.max_inventory_usd,
                "auto_rotate": self.auto_rotate,
                "rotation_interval_min": round(self.rotation_interval_sec / 60.0, 1),
                "trades_target_per_pair": self.trades_target_per_pair,
                "maker_fee_bps": round(self.maker_fee_rate * 10000, 1),
                "taker_fee_bps": round(self.taker_fee_rate * 10000, 1)
            }
        }
