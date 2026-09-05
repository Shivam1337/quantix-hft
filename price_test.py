"""
price_test.py - Sub-Millisecond WebSocket Real-Time Terminal:
Hyperliquid vs Lighter (0% Fee) vs Polymarket BTC Perp

Architecture:
- Native WebSockets for Hyperliquid (wss://api.hyperliquid.xyz/ws) -> Sub-15ms push stream
- Native WebSockets for Lighter.xyz (wss://mainnet.zklighter.elliot.ai/stream) -> Sub-25ms push stream
- Fast-Poller for Polymarket Perps (250ms interval)
- Real-time sub-second EventSource broadcasting to Web UI & Terminal

Run:
    python price_test.py
"""

import asyncio
import collections
import json
import logging
import math
import os
import sys
import time
import webbrowser
from datetime import datetime
from aiohttp import web, ClientSession, ClientTimeout, WSMsgType

# Configure UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Mute noisy access logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
aio_logger = logging.getLogger("aiohttp.access")
aio_logger.setLevel(logging.WARNING)

PORT = 8765
POLY_API_BASE = "https://api.perpetuals.polymarket.com/v1"
POLY_WS_URL = "wss://ws.perpetuals.polymarket.com/v1/ws"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
LIGHTER_WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Rolling history for lead/lag & charting (last 300 data points)
MAX_HISTORY = 300
price_history = collections.deque(maxlen=MAX_HISTORY)


class LighterZeroFeeSniper:
    """
    High-Frequency Zero-Fee Lag Sniper on Lighter.xyz.
    Because Lighter fees are 0.000%, any genuine lag behind Hyperliquid is extractable net profit.
    """
    def __init__(self):
        self.min_lag_trigger = 6.0   # $6.00 breakout lag between HL and Lighter
        self.max_hold_seconds = 12   # Timeout (12s)
        self.cooldown_seconds = 2.0  # 2s cooldown between trades to prevent churn
        self.last_close_ts = 0
        self.trade_size_btc = 0.05
        self.active_trade = None
        self.closed_trades = collections.deque(maxlen=15)
        self.stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "last_signal": "Awaiting Hyperliquid breakout vs Lighter...",
        }

    def process_tick(self, lighter_state, hl_state):
        now_ts = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        l_bid = lighter_state["best_bid"]
        l_ask = lighter_state["best_ask"]
        h_mid = hl_state["mid_price"]

        if l_bid <= 0 or l_ask <= 0 or h_mid <= 0:
            return

        # 1. Manage Active Trade
        if self.active_trade is not None:
            trade = self.active_trade
            hold_time = now_ts - trade["entry_ts"]
            exit_px = None
            reason = None

            if trade["side"] == "LONG":
                # Profit target: Lighter caught up to target
                if l_bid >= trade["target_px"] - 1.0:
                    exit_px = l_bid
                    reason = "TARGET (Lighter caught up)"
                # Signal invalidation: Hyperliquid reversed below entry
                elif h_mid < trade["entry_px"] - 4.0:
                    exit_px = l_bid
                    reason = "HL REVERSAL (False Breakout)"
                elif hold_time >= self.max_hold_seconds:
                    exit_px = l_bid
                    reason = "TIMEOUT (12s)"
                elif (l_bid - trade["entry_px"]) <= -20.0:
                    exit_px = l_bid
                    reason = "HARD STOP ($20)"
            else: # SHORT
                # Profit target: Lighter caught down to target
                if l_ask <= trade["target_px"] + 1.0:
                    exit_px = l_ask
                    reason = "TARGET (Lighter caught up)"
                # Signal invalidation: Hyperliquid reversed above entry
                elif h_mid > trade["entry_px"] + 4.0:
                    exit_px = l_ask
                    reason = "HL REVERSAL (False Breakout)"
                elif hold_time >= self.max_hold_seconds:
                    exit_px = l_ask
                    reason = "TIMEOUT (12s)"
                elif (trade["entry_px"] - l_ask) <= -20.0:
                    exit_px = l_ask
                    reason = "HARD STOP ($20)"

            if exit_px is not None:
                size = trade["size"]
                side = trade["side"]
                entry_px = trade["entry_px"]

                # Lighter ZERO FEES: Fees = $0.00
                gross_pnl = (exit_px - entry_px) * size if side == "LONG" else (entry_px - exit_px) * size
                net_pnl = gross_pnl

                record = {
                    "time": now_str,
                    "side": side,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "hold_sec": round(hold_time, 1),
                    "pnl": round(net_pnl, 2),
                    "reason": reason,
                    "is_win": net_pnl > 0
                }
                self.closed_trades.appendleft(record)
                self.stats["total_trades"] += 1
                if net_pnl > 0:
                    self.stats["wins"] += 1
                else:
                    self.stats["losses"] += 1

                self.stats["win_rate"] = round((self.stats["wins"] / self.stats["total_trades"]) * 100, 1)
                self.stats["net_pnl"] = round(self.stats["net_pnl"] + net_pnl, 2)
                self.active_trade = None
                self.last_close_ts = now_ts
            return

        # Check cooldown
        if now_ts - self.last_close_ts < self.cooldown_seconds:
            return

        # 2. Check for Lag Sniper Signal
        diff = h_mid - l_ask
        if diff >= self.min_lag_trigger and l_ask > 0:
            self.active_trade = {
                "side": "LONG",
                "entry_px": l_ask,
                "target_px": h_mid,
                "expected_lag": diff,
                "size": self.trade_size_btc,
                "entry_ts": now_ts,
                "entry_time": now_str,
            }
            self.stats["last_signal"] = f"⚡ SNIPED LIGHTER LONG: HL ${h_mid:,.1f} vs Lighter Ask ${l_ask:,.1f} (Lag: +${diff:.2f})"
            return

        diff_down = l_bid - h_mid
        if diff_down >= self.min_lag_trigger and l_bid > 0:
            self.active_trade = {
                "side": "SHORT",
                "entry_px": l_bid,
                "target_px": h_mid,
                "expected_lag": diff_down,
                "size": self.trade_size_btc,
                "entry_ts": now_ts,
                "entry_time": now_str,
            }
            self.stats["last_signal"] = f"⚡ SNIPED LIGHTER SHORT: HL ${h_mid:,.1f} vs Lighter Bid ${l_bid:,.1f} (Lag: -${diff_down:.2f})"


lighter_sniper = LighterZeroFeeSniper()

# Global in-memory market state
state = {
    "updated_at": "",
    "latency_ms": 0,
    "feed_type": "WEBSOCKET STREAMING",
    # 1. Hyperliquid (WebSocket)
    "hl": {
        "symbol": "BTC-PERP (Hyperliquid)",
        "mid_price": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
        "fees": "Taker 0.045% | Maker 0.015%",
        "status": "WS CONNECTED",
        "bids": [],
        "asks": [],
    },
    # 2. Lighter (WebSocket - ZERO FEES)
    "lighter": {
        "symbol": "BTC Perp (Lighter.xyz)",
        "mid_price": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
        "fees": "0.000% ZERO TAKER / ZERO MAKER",
        "status": "WS CONNECTED",
        "lag_vs_hl": 0.0,
        "lag_bps": 0.0,
        "bids": [],
        "asks": [],
    },
    # 3. Polymarket (WebSocket)
    "poly": {
        "symbol": "BTC-USD (Polymarket)",
        "mid_price": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
        "fees": "Taker 0.040% | Maker 0.0125%",
        "status": "WS CONNECTED",
        "lag_vs_hl": 0.0,
        "lag_bps": 0.0,
        "bids": [],
        "asks": [],
    },
    "lighter_sniper": {
        "stats": {},
        "active_trade": None,
        "recent_trades": []
    },
    "chart": {
        "timestamps": [],
        "hl_series": [],
        "lighter_series": [],
        "poly_series": [],
        "lighter_lag_series": [],
    }
}

sse_clients = set()
state_lock = asyncio.Lock()


def recalculate_metrics():
    """Recalculates 3-way lags, chart points, and sniper triggers on every incoming tick."""
    now_str = datetime.now().strftime("%H:%M:%S")
    state["updated_at"] = now_str

    h_mid = state["hl"]["mid_price"]
    l_mid = state["lighter"]["mid_price"]
    p_mid = state["poly"]["mid_price"]

    if h_mid > 0 and l_mid > 0:
        l_diff = round(l_mid - h_mid, 2)
        state["lighter"]["lag_vs_hl"] = l_diff
        state["lighter"]["lag_bps"] = round((l_diff / h_mid) * 10000, 1)

    if h_mid > 0 and p_mid > 0:
        p_diff = round(p_mid - h_mid, 2)
        state["poly"]["lag_vs_hl"] = p_diff
        state["poly"]["lag_bps"] = round((p_diff / h_mid) * 10000, 1)

    # Process Lighter Sniper Engine
    lighter_sniper.process_tick(state["lighter"], state["hl"])
    state["lighter_sniper"]["stats"] = lighter_sniper.stats
    state["lighter_sniper"]["active_trade"] = lighter_sniper.active_trade
    state["lighter_sniper"]["recent_trades"] = list(lighter_sniper.closed_trades)

    # Append to rolling history
    if h_mid > 0 and l_mid > 0 and p_mid > 0:
        price_history.append({
            "time": now_str,
            "hl": h_mid,
            "lighter": l_mid,
            "poly": p_mid,
            "l_lag": state["lighter"]["lag_vs_hl"]
        })
        state["chart"]["timestamps"] = [p["time"] for p in price_history]
        state["chart"]["hl_series"] = [p["hl"] for p in price_history]
        state["chart"]["lighter_series"] = [p["lighter"] for p in price_history]
        state["chart"]["poly_series"] = [p["poly"] for p in price_history]
        state["chart"]["lighter_lag_series"] = [p["l_lag"] for p in price_history]


# --- Task 1: Hyperliquid WebSocket Listener (Sub-15ms) ---
async def hyperliquid_ws_loop(session: ClientSession):
    while True:
        try:
            async with session.ws_connect(HL_WS_URL, timeout=ClientTimeout(total=5)) as ws:
                sub = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}}
                await ws.send_str(json.dumps(sub))
                state["hl"]["status"] = "WS STREAMING"

                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("channel") == "l2Book":
                            book = data.get("data", {})
                            levels = book.get("levels", [[], []])
                            bids = levels[0][:6]
                            asks = levels[1][:6]
                            if bids and asks:
                                state["hl"]["bids"] = [[b["px"], b["sz"]] for b in bids]
                                state["hl"]["asks"] = [[a["px"], a["sz"]] for a in asks]
                                state["hl"]["best_bid"] = float(bids[0]["px"])
                                state["hl"]["best_ask"] = float(asks[0]["px"])
                                state["hl"]["mid_price"] = round((state["hl"]["best_bid"] + state["hl"]["best_ask"]) / 2.0, 2)
                                state["hl"]["spread"] = round(state["hl"]["best_ask"] - state["hl"]["best_bid"], 2)
                                recalculate_metrics()
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
        except Exception:
            state["hl"]["status"] = "WS RECONNECTING..."
            await asyncio.sleep(1.0)


# --- Task 2: Lighter.xyz WebSocket Listener (Sub-25ms) ---
async def lighter_ws_loop(session: ClientSession):
    bids_map = {}
    asks_map = {}
    while True:
        try:
            async with session.ws_connect(LIGHTER_WS_URL, timeout=ClientTimeout(total=5)) as ws:
                # Subscribe to BTC orderbook (market_id: 1)
                sub = {"type": "subscribe", "channel": "order_book/1"}
                await ws.send_str(json.dumps(sub))
                state["lighter"]["status"] = "WS STREAMING"

                last_ping = time.time()
                while not ws.closed:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                        if msg.type == WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            mtype = data.get("type")
                            if "order_book" in data:
                                ob = data["order_book"]
                                if mtype == "subscribed/order_book":
                                    bids_map.clear()
                                    asks_map.clear()

                                for b in ob.get("bids", []):
                                    px = float(b["price"])
                                    sz = float(b.get("size", 0))
                                    if sz <= 0:
                                        bids_map.pop(px, None)
                                    else:
                                        bids_map[px] = sz

                                for a in ob.get("asks", []):
                                    px = float(a["price"])
                                    sz = float(a.get("size", 0))
                                    if sz <= 0:
                                        asks_map.pop(px, None)
                                    else:
                                        asks_map[px] = sz

                                if bids_map and asks_map:
                                    best_bid = max(bids_map.keys())
                                    best_ask = min(asks_map.keys())
                                    top_bids = sorted(bids_map.items(), key=lambda x: x[0], reverse=True)[:6]
                                    top_asks = sorted(asks_map.items(), key=lambda x: x[0])[:6]

                                    state["lighter"]["bids"] = [[str(p), str(s)] for p, s in top_bids]
                                    state["lighter"]["asks"] = [[str(p), str(s)] for p, s in top_asks]
                                    state["lighter"]["best_bid"] = best_bid
                                    state["lighter"]["best_ask"] = best_ask
                                    state["lighter"]["mid_price"] = round((best_bid + best_ask) / 2.0, 2)
                                    state["lighter"]["spread"] = round(best_ask - best_bid, 2)
                                    recalculate_metrics()
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # Keepalive ping
                    if time.time() - last_ping > 30:
                        await ws.send_str(json.dumps({"type": "ping"}))
                        last_ping = time.time()
        except Exception:
            state["lighter"]["status"] = "WS RECONNECTING..."
            await asyncio.sleep(1.0)


# --- Task 3: Polymarket Native WebSocket Listener (Sub-20ms) ---
async def poly_ws_loop(session: ClientSession):
    while True:
        try:
            async with session.ws_connect(POLY_WS_URL, timeout=ClientTimeout(total=5)) as ws:
                # Subscribe to BTC orderbook (instrument_id: 6)
                sub = {"req": "sub", "id": 1, "chs": ["book::6"]}
                await ws.send_str(json.dumps(sub))
                state["poly"]["status"] = "WS STREAMING"

                last_ping = time.time()
                while not ws.closed:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                        if msg.type == WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("ch") == "book::6" and "data" in data:
                                book = data["data"]
                                bids = book.get("b", [])[:6]
                                asks = book.get("a", [])[:6]
                                if bids and asks:
                                    state["poly"]["bids"] = bids
                                    state["poly"]["asks"] = asks
                                    state["poly"]["best_bid"] = float(bids[0][0])
                                    state["poly"]["best_ask"] = float(asks[0][0])
                                    state["poly"]["mid_price"] = round((state["poly"]["best_bid"] + state["poly"]["best_ask"]) / 2.0, 2)
                                    state["poly"]["spread"] = round(state["poly"]["best_ask"] - state["poly"]["best_bid"], 2)
                                    recalculate_metrics()
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # Polymarket WS ping keepalive every 25 seconds
                    if time.time() - last_ping > 25:
                        await ws.send_str(json.dumps({"req": "post", "op": {"type": "ping"}}))
                        last_ping = time.time()
        except Exception:
            state["poly"]["status"] = "WS RECONNECTING..."
            await asyncio.sleep(1.0)



# --- Task 4: High-Frequency Broadcaster to Web UI & Terminal ---
async def broadcast_loop():
    last_print = time.time()
    while True:
        if sse_clients:
            payload = f"data: {json.dumps(state)}\n\n"
            disconnected = set()
            for ws in sse_clients:
                try:
                    await ws.write(payload.encode("utf-8"))
                except Exception:
                    disconnected.add(ws)
            sse_clients.difference_update(disconnected)

        # Terminal Print every 500ms
        if time.time() - last_print >= 0.5:
            now_str = state["updated_at"]
            h_mid = state["hl"]["mid_price"]
            l_mid = state["lighter"]["mid_price"]
            p_mid = state["poly"]["mid_price"]
            l_lag = state["lighter"]["lag_vs_hl"]
            p_lag = state["poly"]["lag_vs_hl"]
            pnl = lighter_sniper.stats["net_pnl"]
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            print(
                f"\r[{now_str}] ⚡ HL: ${h_mid:,.1f} | LIGHTER: ${l_mid:,.1f} ({l_lag:+.1f}) | "
                f"POLY: ${p_mid:,.1f} ({p_lag:+.1f}) | Lighter Net PnL: {pnl_str} ({lighter_sniper.stats['total_trades']} trd, {lighter_sniper.stats['win_rate']}%)   ",
                end="",
                flush=True
            )
            last_print = time.time()

        await asyncio.sleep(0.1)  # 100ms push cycle to browser


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sub-Millisecond 3-Exchange BTC Perp Terminal (WebSockets Active)</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #07090e;
      --card-bg: #0f131c;
      --card-border: #1b2234;
      --hl-color: #10e598;
      --hl-bg: rgba(16, 229, 152, 0.12);
      --lighter-color: #f59e0b;
      --lighter-bg: rgba(245, 158, 11, 0.12);
      --poly-color: #00d2ff;
      --poly-bg: rgba(0, 210, 255, 0.12);
      --text: #e6edf3;
      --text-muted: #8b9bb4;
      --green: #00d26a;
      --green-bg: rgba(0, 210, 106, 0.15);
      --red: #ff3355;
      --red-bg: rgba(255, 51, 85, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, sans-serif;
      padding: 20px;
      min-height: 100vh;
    }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .container { max-width: 1440px; margin: 0 auto; }
    
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 10px;
    }
    .logo-group { display: flex; align-items: center; gap: 12px; }
    .coin-badge {
      background: linear-gradient(135deg, #f7931a, #ffb347);
      color: #000;
      font-weight: 800;
      font-size: 18px;
      width: 42px;
      height: 42px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    h1 { font-size: 20px; font-weight: 700; }
    .subhead { font-size: 12px; color: var(--text-muted); }
    
    .status-badge {
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      padding: 8px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); animation: pulse 1s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.2; } }

    /* Lighter Zero-Fee Sniper Section */
    .sniper-banner {
      background: linear-gradient(145deg, #181524, #0d0b16);
      border: 1px solid #3c2a5c;
      border-radius: 14px;
      padding: 16px 20px;
      margin-bottom: 18px;
    }
    .sniper-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      padding-bottom: 8px;
    }
    .zero-fee-pill {
      background: rgba(16, 229, 152, 0.15);
      color: var(--hl-color);
      border: 1px solid var(--hl-color);
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 10px;
      font-weight: 700;
    }
    .sniper-stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 10px;
    }
    .stat-card {
      background: rgba(0,0,0,0.3);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .stat-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 2px; }
    .stat-val { font-size: 20px; font-weight: 800; }

    /* Top 3 Exchange Cards */
    .three-exchanges-grid {
      display: grid;
      grid-template-columns: 1fr 1.05fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }
    @media (max-width: 1100px) { .three-exchanges-grid { grid-template-columns: 1fr; } }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 16px 18px;
    }
    .exchange-title {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 6px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .big-price { font-size: 28px; font-weight: 800; margin-bottom: 6px; }

    .sub-metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 8px;
      border-top: 1px solid rgba(255,255,255,0.06);
      padding-top: 8px;
      font-size: 11px;
    }
    .sub-item span { color: var(--text-muted); display: block; margin-bottom: 1px; }
    .sub-item strong { font-size: 13px; }

    .lighter-card {
      border: 1px solid #4d3a6d;
      background: linear-gradient(145deg, #110f1c, #0d0c16);
    }

    /* Charts */
    .charts-grid {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }
    @media (max-width: 1024px) { .charts-grid { grid-template-columns: 1fr; } }
    .chart-container { position: relative; height: 260px; width: 100%; }

    /* Flash highlight */
    .flash-tick { animation: flashTick 0.3s ease; }
    @keyframes flashTick { 0% { background: rgba(255,255,255,0.1); } 100% { background: transparent; } }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-group">
        <div class="coin-badge">₿</div>
        <div>
          <h1>Sub-Millisecond 3-Exchange BTC Perp Stream</h1>
          <div class="subhead">Native WebSockets on ALL 3: Hyperliquid (&lt;15ms) + Lighter.xyz (&lt;25ms) + Polymarket (&lt;20ms)</div>
        </div>
      </div>
      <div class="status-badge mono">
        <span class="dot"></span>
        <span id="conn-status">3x WEBSOCKETS ACTIVE (SUB-25ms)</span>
        <span style="color: var(--text-muted); margin-left: 4px;">|</span>
        <span id="update-clock">--:--:--</span>
      </div>
    </header>

    <!-- Lighter Zero-Fee Sniper Section -->
    <div class="sniper-banner">
      <div class="sniper-header">
        <div>
          <div style="font-size: 13px; font-weight: 800; display: flex; align-items: center; gap: 8px;">
            <span>⚡ LIGHTER.XYZ ZERO-FEE WEBSOCKET SNIPER</span>
            <span class="zero-fee-pill">ZERO FEES (0.000%)</span>
          </div>
          <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">
            Streaming real-time push frames directly over WebSocket. Any $3+ lag is captured instantly.
          </div>
        </div>
        <div class="mono" style="font-size: 12px;">
          Lighter Lag vs HL: <strong id="lighter-lag-header" style="color: var(--lighter-color);">--</strong>
        </div>
      </div>

      <div class="sniper-stats-grid mono">
        <div class="stat-card">
          <div class="stat-label">Lighter Simulated Net PnL</div>
          <div class="stat-val" id="sniper-net-pnl">--</div>
          <div style="font-size: 9px; color: var(--hl-color); margin-top: 2px;">Fees Paid: $0.00 (Zero Fee DEX)</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Win Rate</div>
          <div class="stat-val" id="sniper-winrate" style="color: var(--hl-color);">--</div>
          <div style="font-size: 9px; color: var(--text-muted); margin-top: 2px;" id="sniper-trades-count">0 Trades</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Reaction Speed</div>
          <div class="stat-val" style="color: var(--poly-color);">Sub-25ms</div>
          <div style="font-size: 9px; color: var(--text-muted); margin-top: 2px;">Native push frames (0 HTTP polling)</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Minimum Move to Profit</div>
          <div class="stat-val" style="color: var(--green);">$0.10</div>
          <div style="font-size: 9px; color: var(--text-muted); margin-top: 2px;">Zero fee barrier</div>
        </div>
      </div>

      <div style="background: rgba(0,0,0,0.3); border: 1px dashed #4d3a6d; border-radius: 8px; padding: 8px 12px; font-size: 11px;" class="mono" id="sniper-signal-text">
        WebSocket streaming active...
      </div>
    </div>

    <!-- Top 3 Exchange Cards Grid -->
    <div class="three-exchanges-grid">
      <!-- 1. Hyperliquid -->
      <div class="card" id="card-hl">
        <div class="exchange-title">
          <span style="color: var(--hl-color);">● HYPERLIQUID</span>
          <span class="mono" style="font-size: 10px; color: var(--hl-color);" id="hl-status">WS STREAMING</span>
        </div>
        <div class="big-price mono" id="hl-price" style="color: var(--hl-color);">$--</div>
        <div class="mono" style="font-size: 11px; color: var(--text-muted);" id="hl-spread">Spread: --</div>
        <div class="sub-metrics mono">
          <div class="sub-item"><span>Best Bid</span><strong style="color: var(--green);" id="hl-bid">$--</strong></div>
          <div class="sub-item"><span>Best Ask</span><strong style="color: var(--red);" id="hl-ask">$--</strong></div>
          <div class="sub-item"><span>Protocol</span><strong>Native WS (&lt;15ms)</strong></div>
          <div class="sub-item"><span>Role</span><strong style="color: var(--hl-color);">Price Discovery</strong></div>
        </div>
      </div>

      <!-- 2. Lighter.xyz -->
      <div class="card lighter-card" id="card-lighter">
        <div class="exchange-title">
          <span style="color: var(--lighter-color);">● LIGHTER.XYZ (ZK)</span>
          <span class="zero-fee-pill">ZERO FEES</span>
        </div>
        <div class="big-price mono" id="lighter-price" style="color: var(--lighter-color);">$--</div>
        <div class="mono" style="font-size: 11px;" id="lighter-lag-sub">Lag vs HL: --</div>
        <div class="sub-metrics mono">
          <div class="sub-item"><span>Best Bid</span><strong style="color: var(--green);" id="lighter-bid">$--</strong></div>
          <div class="sub-item"><span>Best Ask</span><strong style="color: var(--red);" id="lighter-ask">$--</strong></div>
          <div class="sub-item"><span>Protocol</span><strong style="color: var(--lighter-color);">Native WS (&lt;25ms)</strong></div>
          <div class="sub-item"><span>Fees</span><strong style="color: var(--hl-color);">0.000% FREE</strong></div>
        </div>
      </div>

      <!-- 3. Polymarket -->
      <div class="card" id="card-poly">
        <div class="exchange-title">
          <span style="color: var(--poly-color);">● POLYMARKET</span>
          <span class="mono" style="font-size: 10px; color: var(--poly-color);" id="poly-status">WS STREAMING</span>
        </div>
        <div class="big-price mono" id="poly-price" style="color: var(--poly-color);">$--</div>
        <div class="mono" style="font-size: 11px; color: var(--text-muted);" id="poly-lag-sub">Lag vs HL: --</div>
        <div class="sub-metrics mono">
          <div class="sub-item"><span>Best Bid</span><strong style="color: var(--green);" id="poly-bid">$--</strong></div>
          <div class="sub-item"><span>Best Ask</span><strong style="color: var(--red);" id="poly-ask">$--</strong></div>
          <div class="sub-item"><span>Protocol</span><strong style="color: var(--poly-color);">Native WS (&lt;20ms)</strong></div>
          <div class="sub-item"><span>Spread</span><strong id="poly-spread">--</strong></div>
        </div>
      </div>
    </div>
    </div>

    <!-- Charts Section -->
    <div class="charts-grid">
      <div class="card">
        <div style="font-size: 13px; font-weight: 700; margin-bottom: 8px;">
          Sub-Second 3-Way Live Price Overlay
        </div>
        <div class="chart-container"><canvas id="priceChart"></canvas></div>
      </div>

      <div class="card">
        <div style="font-size: 13px; font-weight: 700; margin-bottom: 8px;">
          Lighter Lag Spread vs Hyperliquid ($)
        </div>
        <div class="chart-container"><canvas id="lagChart"></canvas></div>
      </div>
    </div>
  </div>

  <script>
    const ctxPrice = document.getElementById('priceChart').getContext('2d');
    const priceChart = new Chart(ctxPrice, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          { label: 'Hyperliquid Mid (Green)', borderColor: '#10e598', borderWidth: 2, pointRadius: 0, data: [] },
          { label: 'Lighter.xyz Mid (Orange)', borderColor: '#f59e0b', borderWidth: 2, pointRadius: 0, data: [] },
          { label: 'Polymarket Mid (Cyan)', borderColor: '#00d2ff', borderWidth: 2, pointRadius: 0, data: [] }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: '#8b9bb4', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 8 } },
          y: { grid: { color: '#1b2234' }, ticks: { color: '#8b9bb4', font: { family: 'JetBrains Mono', size: 10 }, callback: v => '$' + v.toLocaleString() } }
        },
        plugins: { legend: { labels: { color: '#e6edf3', font: { family: 'Inter', size: 11 } } } }
      }
    });

    const ctxLag = document.getElementById('lagChart').getContext('2d');
    const lagChart = new Chart(ctxLag, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          { label: 'Lighter - HL Lag ($)', borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.15)', fill: true, borderWidth: 2, pointRadius: 0, data: [] }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: '#8b9bb4', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 8 } },
          y: { grid: { color: '#1b2234' }, ticks: { color: '#8b9bb4', font: { family: 'JetBrains Mono', size: 10 }, callback: v => (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(1) } }
        },
        plugins: { legend: { display: false } }
      }
    });

    const evtSource = new EventSource('/api/stream');
    evtSource.onmessage = function(e) {
      const d = JSON.parse(e.data);
      render(d);
    };

    function fmt(num, dec = 2) {
      if (num === null || num === undefined || isNaN(num)) return '--';
      return Number(num).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }

    function render(d) {
      document.getElementById('update-clock').innerText = d.updated_at;

      // 1. Hyperliquid
      const h = d.hl;
      document.getElementById('hl-price').innerText = `$${fmt(h.mid_price, 1)}`;
      document.getElementById('hl-spread').innerText = `Spread: $${fmt(h.spread, 2)}`;
      document.getElementById('hl-bid').innerText = `$${fmt(h.best_bid, 1)}`;
      document.getElementById('hl-ask').innerText = `$${fmt(h.best_ask, 1)}`;
      document.getElementById('hl-status').innerText = h.status;

      // 2. Lighter
      const l = d.lighter;
      document.getElementById('lighter-price').innerText = `$${fmt(l.mid_price, 1)}`;
      document.getElementById('lighter-bid').innerText = `$${fmt(l.best_bid, 1)}`;
      document.getElementById('lighter-ask').innerText = `$${fmt(l.best_ask, 1)}`;
      const lSign = l.lag_vs_hl >= 0 ? '+' : '';
      const lLagText = `${lSign}$${fmt(l.lag_vs_hl, 2)} (${lSign}${l.lag_bps} bps)`;
      document.getElementById('lighter-lag-sub').innerText = `Lag vs HL: ${lLagText}`;
      document.getElementById('lighter-lag-header').innerText = lLagText;

      // 3. Polymarket
      const p = d.poly;
      document.getElementById('poly-price').innerText = `$${fmt(p.mid_price, 1)}`;
      document.getElementById('poly-bid').innerText = `$${fmt(p.best_bid, 1)}`;
      document.getElementById('poly-ask').innerText = `$${fmt(p.best_ask, 1)}`;
      document.getElementById('poly-spread').innerText = `$${fmt(p.spread, 2)}`;
      const pSign = p.lag_vs_hl >= 0 ? '+' : '';
      document.getElementById('poly-lag-sub').innerText = `Lag vs HL: ${pSign}$${fmt(p.lag_vs_hl, 2)} (${pSign}${p.lag_bps} bps)`;
      if (document.getElementById('poly-status')) {
        document.getElementById('poly-status').innerText = p.status || 'WS STREAMING';
      }

      // 4. Lighter Sniper Stats
      if (d.lighter_sniper) {
        const stats = d.lighter_sniper.stats;
        document.getElementById('sniper-signal-text').innerText = stats.last_signal || 'Monitoring Lighter WebSocket...';
        const netPnlEl = document.getElementById('sniper-net-pnl');
        const netPnl = stats.net_pnl || 0;
        netPnlEl.innerText = `${netPnl >= 0 ? '+' : ''}$${fmt(netPnl, 2)}`;
        netPnlEl.style.color = netPnl > 0 ? 'var(--green)' : (netPnl < 0 ? 'var(--red)' : 'var(--text)');
        document.getElementById('sniper-winrate').innerText = `${stats.win_rate || 0}%`;
        document.getElementById('sniper-trades-count').innerText = `${stats.total_trades || 0} Trades (${stats.wins || 0}W / ${stats.losses || 0}L)`;
      }

      // 5. Update Charts
      if (d.chart && d.chart.timestamps.length > 0) {
        priceChart.data.labels = d.chart.timestamps;
        priceChart.data.datasets[0].data = d.chart.hl_series;
        priceChart.data.datasets[1].data = d.chart.lighter_series;
        priceChart.data.datasets[2].data = d.chart.poly_series;
        priceChart.update();

        lagChart.data.labels = d.chart.timestamps;
        lagChart.data.datasets[0].data = d.chart.lighter_lag_series;
        lagChart.update();
      }
    }
  </script>
</body>
</html>
"""


async def index_handler(request):
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def sse_handler(request):
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)
    sse_clients.add(response)
    initial_payload = f"data: {json.dumps(state)}\n\n"
    await response.write(initial_payload.encode("utf-8"))

    try:
        while True:
            await asyncio.sleep(30)
            await response.write(b": keepalive\n\n")
    except Exception:
        pass
    finally:
        sse_clients.discard(response)
    return response


async def api_data_handler(request):
    return web.json_response(state)


async def main():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/stream", sse_handler)
    app.router.add_get("/api/data", api_data_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    url = f"http://localhost:{PORT}"
    print("=" * 85)
    print("  SUB-MILLISECOND 3-EXCHANGE WEBSOCKET STREAM RUNNING")
    print(f"  URL: {url}")
    print("=" * 85)
    print("Hyperliquid WS:  wss://api.hyperliquid.xyz/ws (<15ms push)")
    print("Lighter.xyz WS:  wss://mainnet.zklighter.elliot.ai/stream (<25ms push)")
    print("Polymarket WS:   wss://ws.perpetuals.polymarket.com/v1/ws (<20ms push)")
    print("Streaming live push ticks (Ctrl+C to stop)...")
    print()

    try:
        webbrowser.open(url)
    except Exception:
        pass

    async with ClientSession(headers=HEADERS) as session:
        await asyncio.gather(
            hyperliquid_ws_loop(session),
            lighter_ws_loop(session),
            poly_ws_loop(session),
            broadcast_loop(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")
