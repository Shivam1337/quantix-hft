"""
FastAPI Server & WebSocket Telemetry Gateway.
Serves the HFT Web Dashboard and streams live market-making state to clients.
"""

import asyncio
import os
import requests
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from trader import LiveHFTTrader
from database import db
from fastapi.responses import Response

app = FastAPI(title="HFT Microstructure Market Making Engine")
trader = LiveHFTTrader()

# Active WebSocket dashboard clients
active_websockets: List[WebSocket] = []


class ConfigRequest(BaseModel):
    coin: str = "PONS"
    initial_capital: float = 50.0
    order_size_usd: float = 10.0
    gamma: float = 0.6
    beta_ofi: float = 0.7
    min_spread_bps: float = 2.0
    min_market_spread_bps: float = 4.5
    max_inventory_usd: float = 30.0
    maker_fee_rate: float = 0.00015
    taker_fee_rate: float = 0.00045
    auto_rotate: bool = True
    rotation_interval_min: float = 15.0
    trades_target_per_pair: int = 12
    dynamic_sizing: bool = True
    min_order_size_usd: float = 10.0
    mode: str = "SIMULATED"


@app.on_event("startup")
async def startup_event():
    # 1. Connect to PostgreSQL
    await db.connect()
    
    # 2. Restore engine state & resume trading if running prior to deployment
    try:
        saved_state = await db.load_engine_state()
        if saved_state:
            recent_fills = await db.get_recent_fills(limit=50)
            recent_rotations = await db.get_recent_rotations(limit=20)
            trader.restore_state(saved_state, recent_fills=recent_fills, recent_rotations=recent_rotations)
            
            # If the engine was actively trading before this deployment/restart, automatically resume!
            if saved_state.get("status") == "RUNNING":
                print(f"[Continuous Deployment] Auto-resuming active trading on {saved_state.get('coin', 'PONS')}...")
                saved_config = saved_state.get("config", {})
                asyncio.create_task(trader.start(config=saved_config, resume=True))
    except Exception as e:
        print(f"[Continuous Deployment Startup Warning] Could not recover state: {e}")

    # 3. Start telemetry broadcast loop
    asyncio.create_task(broadcast_telemetry_loop())


@app.on_event("shutdown")
async def shutdown_event():
    # Save exact in-flight trading state so next container resumes seamlessly
    try:
        await trader.save_state()
    except Exception:
        pass
    await db.close()


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
    await trader.stop()
    trader.reset_account()
    await trader.save_state()
    return {"status": "success", "message": "Account reset to initial capital ($50.00)"}


@app.post("/api/rotate")
async def force_rotate():
    """Manually triggers immediate inventory offload and pair rotation."""
    await trader.force_rotate("Manual user trigger")
    return {"status": "success", "message": f"Offload and rotation initiated for {trader.coin}"}


@app.get("/api/screener")
async def run_screener():
    """Returns top capacity-constrained coins with high volume and wide spreads."""
    try:
        def _scan():
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
            return results

        results = await asyncio.to_thread(_scan)
        return {"candidates": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/rotations")
async def get_rotations(limit: int = 20):
    """Returns coin rotation history."""
    rotations = await db.get_rotations(session_id=trader.session_id, limit=limit)
    return {"rotations": rotations}


@app.get("/api/history/sessions")
async def get_sessions(limit: int = 20):
    """Returns past trading runs stored in PostgreSQL."""
    sessions = await db.get_sessions(limit=limit)
    return {"sessions": sessions}


@app.get("/api/history/fills")
async def get_fills(session_id: Optional[int] = None, limit: int = 100):
    """Returns execution fills stored in PostgreSQL."""
    fills = await db.get_fills(session_id=session_id, limit=limit)
    return {"fills": fills}


@app.get("/api/history/export")
async def export_session_csv(session_id: Optional[int] = None):
    """Downloads execution fills as a CSV for external analysis."""
    if session_id is None:
        if trader.session_id:
            session_id = trader.session_id
        else:
            sessions = await db.get_sessions(limit=1)
            if sessions:
                session_id = sessions[0]["id"]

    if session_id is None:
        return Response(
            content="timestamp,coin,side,price,size,notional,fee,fee_type,inventory_after,cash_after\n",
            media_type="text/csv"
        )

    csv_data = await db.export_session_csv(session_id=session_id)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}_fills.csv"}
    )


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
