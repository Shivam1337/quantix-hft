"""
Market Feed Manager.
Runs native asynchronous WebSocket stream consumers for Hyperliquid, Lighter.xyz, and Polymarket.
"""
import time
import json
import logging
import asyncio
from aiohttp import ClientSession, ClientTimeout, WSMsgType
from app.config import (
    BINANCE_WS_URL,
    BYBIT_WS_URL, BYBIT_SUB_PAYLOAD,
    OKX_WS_URL, OKX_SUB_PAYLOAD,
    HL_WS_URL, HL_SUB_PAYLOAD,
    LIGHTER_WS_URL, LIGHTER_SUB_PAYLOAD,
    POLY_WS_URL, POLY_SUB_PAYLOAD,
    DEFAULT_HEADERS
)
from app.core.state_manager import state_manager

logger = logging.getLogger("market_feed")



async def binance_ws_task(session: ClientSession):
    """
    Binance Futures BTCUSDT bookTicker WebSocket push listener (<10ms latency).
    Streams real-time top-of-book price discovery pushes.
    """
    while True:
        try:
            async with session.ws_connect(BINANCE_WS_URL, timeout=5.0) as ws:
                state_manager.update_binance([], [], 0.0, 0.0, status="WS STREAMING")

                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if "b" in data and "a" in data:
                            b_px = float(data["b"])
                            a_px = float(data["a"])
                            b_sz = data.get("B", "0")
                            a_sz = data.get("A", "0")
                            bids = [[str(b_px), str(b_sz)]]
                            asks = [[str(a_px), str(a_sz)]]
                            state_manager.update_binance(
                                bids,
                                asks,
                                b_px,
                                a_px,
                                status="WS STREAMING",
                                exchange_timestamp_ms=data.get("E") or data.get("T"),
                                sequence=data.get("u"),
                            )
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Binance feed reconnecting: %s", exc)
            state_manager.update_binance([], [], 0.0, 0.0, status="WS RECONNECTING...")
            await asyncio.sleep(1.0)


async def bybit_ws_task(session: ClientSession):
    """
    Bybit Linear BTCUSDT WebSocket push listener (<10ms latency).
    Streams real-time top-of-book and ticker updates for global derivatives discovery.
    """
    last_bid = 0.0
    last_ask = 0.0
    while True:
        try:
            async with session.ws_connect(BYBIT_WS_URL, timeout=5.0) as ws:
                await ws.send_str(json.dumps(BYBIT_SUB_PAYLOAD))
                state_manager.update_bybit([], [], 0.0, 0.0, status="WS STREAMING")

                last_ping = time.time()
                while not ws.closed:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                        if msg.type == WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if "data" in data and "topic" in data:
                                t = data["data"]
                                if isinstance(t, dict):
                                    if "bid1Price" in t and t["bid1Price"]:
                                        try:
                                            last_bid = float(t["bid1Price"])
                                        except (ValueError, TypeError):
                                            pass
                                    if "ask1Price" in t and t["ask1Price"]:
                                        try:
                                            last_ask = float(t["ask1Price"])
                                        except (ValueError, TypeError):
                                            pass
                                    if last_bid > 0 and last_ask > 0:
                                        bids = [[str(last_bid), str(t.get("bid1Size", "0"))]]
                                        asks = [[str(last_ask), str(t.get("ask1Size", "0"))]]
                                        state_manager.update_bybit(
                                            bids,
                                            asks,
                                            last_bid,
                                            last_ask,
                                            status="WS STREAMING",
                                            exchange_timestamp_ms=data.get("ts") or t.get("ts"),
                                            sequence=data.get("cs") or t.get("cs"),
                                        )
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # 20s ping keepalive for Bybit v5
                    if time.time() - last_ping > 20:
                        await ws.send_str(json.dumps({"op": "ping"}))
                        last_ping = time.time()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Bybit feed reconnecting: %s", exc)
            state_manager.update_bybit([], [], 0.0, 0.0, status="WS RECONNECTING...")
            await asyncio.sleep(1.0)


async def okx_ws_task(session: ClientSession):
    """
    OKX Perpetual BTC-USDT-SWAP WebSocket push listener (<12ms latency).
    Streams real-time top-of-book and ticker updates for global derivatives discovery.
    """
    while True:
        try:
            async with session.ws_connect(OKX_WS_URL, timeout=5.0) as ws:
                await ws.send_str(json.dumps(OKX_SUB_PAYLOAD))
                state_manager.update_okx([], [], 0.0, 0.0, status="WS STREAMING")

                last_ping = time.time()
                while not ws.closed:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                        if msg.type == WSMsgType.TEXT:
                            if msg.data == "pong":
                                continue
                            data = json.loads(msg.data)
                            if "data" in data and len(data["data"]) > 0:
                                t = data["data"][0]
                                bid_px = float(t.get("bidPx", 0.0))
                                ask_px = float(t.get("askPx", 0.0))
                                bid_sz = t.get("bidSz", "0")
                                ask_sz = t.get("askSz", "0")
                                if bid_px > 0 and ask_px > 0:
                                    bids = [[str(bid_px), str(bid_sz)]]
                                    asks = [[str(ask_px), str(ask_sz)]]
                                    state_manager.update_okx(
                                        bids,
                                        asks,
                                        bid_px,
                                        ask_px,
                                        status="WS STREAMING",
                                        exchange_timestamp_ms=t.get("ts"),
                                        sequence=t.get("seqId"),
                                    )
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # 20s ping keepalive for OKX
                    if time.time() - last_ping > 20:
                        await ws.send_str("ping")
                        last_ping = time.time()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("OKX feed reconnecting: %s", exc)
            state_manager.update_okx([], [], 0.0, 0.0, status="WS RECONNECTING...")
            await asyncio.sleep(1.0)



async def hyperliquid_ws_task(session: ClientSession):
    """
    Hyperliquid BTC Perpetual WebSocket push listener (<15ms latency).
    """
    while True:
        try:
            async with session.ws_connect(HL_WS_URL, timeout=5.0) as ws:
                await ws.send_str(json.dumps(HL_SUB_PAYLOAD))
                state_manager.update_hl([], [], status="WS STREAMING")

                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("channel") == "l2Book":
                            book = data.get("data", {})
                            levels = book.get("levels", [[], []])
                            bids = [[b["px"], b["sz"]] for b in levels[0][:6]]
                            asks = [[a["px"], a["sz"]] for a in levels[1][:6]]
                            state_manager.update_hl(
                                bids,
                                asks,
                                status="WS STREAMING",
                                exchange_timestamp_ms=book.get("time") or data.get("time"),
                                sequence=book.get("seqNum") or data.get("seqNum"),
                            )
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Hyperliquid feed reconnecting: %s", e)
            state_manager.update_hl([], [], status="WS RECONNECTING...")
            await asyncio.sleep(1.0)


async def lighter_ws_task(session: ClientSession):
    """
    Lighter.xyz BTC Perpetual WebSocket listener (<25ms latency).
    Maintains an in-memory L2 order book supporting both initial snapshots and delta updates.
    """
    while True:
        try:
            bids_map = {}
            asks_map = {}
            async with session.ws_connect(LIGHTER_WS_URL, timeout=5.0) as ws:
                await ws.send_str(json.dumps(LIGHTER_SUB_PAYLOAD))
                state_manager.reset_lighter_orderbook(status="WS STREAMING")

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

                                    bids_fmt = [[str(p), str(s)] for p, s in top_bids]
                                    asks_fmt = [[str(p), str(s)] for p, s in top_asks]
                                    state_manager.update_lighter(
                                        bids_fmt,
                                        asks_fmt,
                                        best_bid,
                                        best_ask,
                                        status="WS STREAMING",
                                        exchange_timestamp_ms=data.get("timestamp") or data.get("ts") or ob.get("timestamp"),
                                        sequence=data.get("sequence") or data.get("seq") or ob.get("sequence"),
                                    )

                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # 30s ping keepalive
                    if time.time() - last_ping > 30:
                        await ws.send_str(json.dumps({"type": "ping"}))
                        last_ping = time.time()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Lighter feed reconnecting: %s", e)
            state_manager.reset_lighter_orderbook(status="WS RECONNECTING...")
            await asyncio.sleep(1.0)


async def polymarket_ws_task(session: ClientSession):
    """
    Polymarket Perps BTC-USD Native WebSocket listener (<20ms latency).
    Subscribes to book::6 channel with 25s ping keepalive.
    """
    while True:
        try:
            async with session.ws_connect(POLY_WS_URL, timeout=5.0) as ws:
                await ws.send_str(json.dumps(POLY_SUB_PAYLOAD))
                state_manager.update_poly([], [], status="WS STREAMING")

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
                                    state_manager.update_poly(
                                        bids,
                                        asks,
                                        status="WS STREAMING",
                                        exchange_timestamp_ms=data.get("timestamp") or book.get("timestamp") or data.get("ts"),
                                        sequence=data.get("sequence") or book.get("sequence") or data.get("seq"),
                                    )
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                    except asyncio.TimeoutError:
                        pass

                    # 25s ping keepalive
                    if time.time() - last_ping > 25:
                        await ws.send_str(json.dumps({"req": "post", "op": {"type": "ping"}}))
                        last_ping = time.time()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Polymarket feed reconnecting: %s", e)
            state_manager.update_poly([], [], status="WS RECONNECTING...")
            await asyncio.sleep(1.0)
