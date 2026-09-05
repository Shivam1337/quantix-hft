"""
FastAPI Server & WebSocket Telemetry Gateway.
Serves the HFT Web Dashboard and streams live market-making state to clients.
"""

import asyncio
import os
import requests
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from trader import LiveHFTTrader

app = FastAPI(title="HFT Microstructure Market Making Engine")
trader = LiveHFTTrader()

# Active WebSocket dashboard clients
active_websockets: List[WebSocket] = []


class ConfigRequest(BaseModel):
    coin: str = "PONS"
    order_size_usd: float = 50.0
    gamma: float = 0.5
    beta_ofi: float = 0.6
    min_spread_bps: float = 2.0
    max_inventory_usd: float = 250.0
    mode: str = "SIMULATED"


@app.on_event("startup")
async def startup_event():
    # Start telemetry broadcast loop
    asyncio.create_task(broadcast_telemetry_loop())


async def broadcast_telemetry_loop():
    """Broadcasts trading telemetry to all connected WebSocket clients at ~4Hz."""
    while True:
        await asyncio.sleep(0.25)
        if not active_websockets:
            continue

        telemetry = trader.get_telemetry()
        disconnected = []
        for ws in active_websockets:
            try:
                await ws.send_json(telemetry)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            if ws in active_websockets:
                active_websockets.remove(ws)


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        # Send initial state immediately
        await websocket.send_json(trader.get_telemetry())
        while True:
            # Keep connection open and listen for optional ping/commands
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
    except Exception:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


@app.get("/api/status")
async def get_status():
    return trader.get_telemetry()


@app.post("/api/start")
async def start_trader(req: ConfigRequest):
    await trader.start(req.dict())
    return {"status": "success", "message": f"Trader started on {req.coin}"}


@app.post("/api/stop")
async def stop_trader():
    await trader.stop()
    return {"status": "success", "message": "Trader stopped"}


@app.post("/api/reset")
async def reset_trader():
    trader.reset_account()
    return {"status": "success", "message": "Account reset to initial capital"}


@app.get("/api/screener")
async def run_screener():
    """Returns top capacity-constrained coins with high volume and wide spreads."""
    try:
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=5).json()
        universe = r[0]["universe"]
        ctxs = r[1]

        candidates = []
        for u, c in zip(universe, ctxs):
            name = u["name"]
            vol = float(c.get("dayNtlVlm", 0))
            mark = float(c.get("markPx", 0))
            if vol > 1000000 and mark > 0:
                candidates.append({"name": name, "vol": vol, "mark": mark})

        candidates.sort(key=lambda x: x["vol"], reverse=True)

        results = []
        for c in candidates[:25]:
            try:
                book = requests.post("https://api.hyperliquid.xyz/info", json={"type": "l2Book", "coin": c["name"]}, timeout=2).json()
                bids = book["levels"][0]
                asks = book["levels"][1]
                if bids and asks:
                    bb = float(bids[0]["px"])
                    ba = float(asks[0]["px"])
                    spread_bps = (ba - bb) / bb * 10000
                    top_depth = float(bids[0]["sz"]) * bb
                    results.append({
                        "name": c["name"],
                        "vol_24h": round(c["vol"], 0),
                        "price": round(c["mark"], 4),
                        "spread_bps": round(spread_bps, 2),
                        "top_depth_usd": round(top_depth, 0)
                    })
            except Exception:
                pass

        results.sort(key=lambda x: x["spread_bps"], reverse=True)
        return {"candidates": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/llm")
async def get_llm_summary(format: str = "markdown"):
    """
    Returns an executive diagnostic report optimized for LLMs and AI monitoring agents.
    Provides real-time P&L, inventory exposure, microstructure signals, and risk alerts.
    """
    t = trader.get_telemetry()
    status = t["status"]
    coin = t["coin"]
    equity = t["equity"]
    net_pnl = t["net_pnl"]
    ret_pct = t["return_pct"]
    inv = t["inventory"]
    inv_usd = t["inventory_usd"]
    max_inv = t["config"]["max_inventory_usd"]
    inv_utilization = abs(inv_usd) / max(max_inv, 1.0) * 100.0
    ofi = t["ofi"]
    vol = t["volatility"]
    fills = t["fills_count"]
    fees = t["total_fees"]
    mid = t["mid_price"]
    spread = t["spread_bps"]
    active_bid = t["active_bid"]
    active_ask = t["active_ask"]

    # Microstructure Assessment
    flow_state = "Neutral"
    if ofi > 1000:
        flow_state = f"Strong Bullish Order Flow (+{ofi:,.0f})"
    elif ofi < -1000:
        flow_state = f"Strong Bearish Order Flow ({ofi:,.0f})"

    inv_risk = "Low"
    if inv_utilization > 75:
        inv_risk = "CRITICAL (Near Circuit Breaker)"
    elif inv_utilization > 40:
        inv_risk = "Moderate (Inventory Skew Active)"

    recent = t["recent_fills"][:5]

    if format == "json":
        return {
            "status": status,
            "asset": coin,
            "financials": {
                "equity_usd": equity,
                "net_pnl_usd": net_pnl,
                "return_pct": ret_pct,
                "cash_usd": t["cash"],
                "total_fees_usd": fees
            },
            "inventory": {
                "contracts": inv,
                "notional_usd": inv_usd,
                "max_limit_usd": max_inv,
                "utilization_pct": round(inv_utilization, 1),
                "risk_level": inv_risk
            },
            "market_microstructure": {
                "mid_price": mid,
                "spread_bps": spread,
                "order_flow_imbalance": ofi,
                "flow_sentiment": flow_state,
                "volatility_bps": vol
            },
            "quoting": {
                "active_bid": active_bid,
                "active_ask": active_ask,
                "quote_size_usd": t["config"]["order_size_usd"]
            },
            "fills": {
                "total_count": fills,
                "recent_fills": recent
            }
        }

    # Markdown format
    md = f"""# Quantix HFT Production Telemetry Report

### 1. System & Financial Summary
- **Status:** `{status}` | **Target Asset:** `{coin}` | **Mode:** `{t['mode']}`
- **Total Equity:** `${equity:,.2f}`
- **Net P&L:** `${net_pnl:+,.2f}` (`{ret_pct:+.2f}%`)
- **Cash Available:** `${t['cash']:,.2f}`
- **Total Maker Fees Paid:** `${fees:.4f}`
- **Total Executions:** `{fills}` fills

### 2. Inventory & Risk Exposure
- **Position:** `{inv:+.4f} {coin}` (`${inv_usd:+,.2f} USD`)
- **Inventory Limit:** `${max_inv:,.2f}` (`{inv_utilization:.1f}%` utilization)
- **Inventory Risk State:** `{inv_risk}`

### 3. Live Microstructure & Order Book
- **Mid Price:** `${mid:.4f}`
- **Market Spread:** `{spread:.2f} bps`
- **Order Flow Imbalance (OFI):** `{ofi:,.1f}` ({flow_state})
- **Rolling Volatility:** `{vol:.1f} bps`
- **Active Post-Only Quotes:**
  - **Bid:** `{f"${active_bid:.4f}" if active_bid else "None (Pull/Risk Limit)"}`
  - **Ask:** `{f"${active_ask:.4f}" if active_ask else "None (Pull/Risk Limit)"}`

### 4. Recent Fills
"""
    if recent:
        for f in recent:
            md += f"- `{f['time']}` | **{f['side']}** | Px: `${f['price']:.4f}` | Sz: `{f['size']}` (${f['notional']:.2f}) | Inv After: `{f['inventory_after']:+.2f}`\n"
    else:
        md += "- No recent fills.\n"

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(md)


# Mount static web dashboard
web_dir = os.path.join(os.path.dirname(__file__), "web")
if not os.path.exists(web_dir):
    os.makedirs(web_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.get("/")
async def serve_index():
    index_file = os.path.join(web_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Web dashboard building..."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
