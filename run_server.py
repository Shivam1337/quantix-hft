"""
Executable Server Launcher.
Starts the Uvicorn HTTP server running the multi-file FastAPI lead-lag engine.

Run:
    python run_server.py
"""
import sys
import os
import uvicorn

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.config import (
    GRACEFUL_SHUTDOWN_SECONDS,
    SERVER_HOST,
    SERVER_PORT,
    UVICORN_GRACEFUL_SHUTDOWN_SECONDS,
)
from app.core.state_manager import state_manager


class GracefulUvicornServer(uvicorn.Server):
    """Tell long-lived app streams to stop before Uvicorn waits on them.

    Uvicorn still owns native SIGINT/SIGTERM behaviour. This tiny synchronous
    hook only flips an in-process flag, allowing SSE generators to finish in
    their next 100ms cycle instead of using the whole request timeout.
    """

    def handle_exit(self, sig, frame):
        state_manager.begin_shutdown()
        super().handle_exit(sig, frame)


def main():
    url = f"http://localhost:{SERVER_PORT}"

    print("=" * 85)
    print("  🔬 BTC PERPETUAL LEAD-LAG MEASUREMENT SERVER RUNNING (PAPER ONLY)")
    print(f"  Web Dashboard:        {url}")
    print(f"  Interactive API Docs: {url}/docs")
    print("=" * 85)
    print("Available REST Query Endpoints:")
    print(f"  ● Market State:       GET {url}/api/market/state")
    print(f"  ● Rapid Prices:       GET {url}/api/market/prices")
    print(f"  ● Top Order Books:    GET {url}/api/market/orderbooks")
    print(f"  ● Trade Decision:     GET {url}/api/trades/decision")
    print(f"  ● Active Position:    GET {url}/api/trades/active")
    print(f"  ● Performance Stats:  GET {url}/api/trades/performance")
    print(f"  ● Closed Trades:      GET {url}/api/trades/history")
    print(f"  ● Lead-Lag Status:    GET {url}/api/analytics/lead-lag")
    print(f"  ● Repricing Events:   GET {url}/api/analytics/repricing-events")
    print(f"  ● Fees Comparison:    GET {url}/api/analytics/fees-comparison")
    print(f"  ● Provider Insights:  GET {url}/api/system/providers")
    print(f"  ● Resource Usage:     GET {url}/api/system/resources")
    print(f"  ● Persistence State:  GET {url}/api/system/persistence")
    print(f"  ● Readiness:          GET {url}/api/system/readiness")

    print(f"  ● System Health:      GET {url}/api/system/health")
    print("=" * 85)
    print("6x Native WebSockets (4 major discovery venues + Polymarket observer + Lighter target):")
    print("  ⚡ Binance Futures:  wss://fstream.binance.com/ws/btcusdt@bookTicker (<10ms)")
    print("  ⚡ Bybit Linear:      wss://stream.bybit.com/v5/public/linear (<10ms)")
    print("  ⚡ OKX Perpetual:     wss://ws.okx.com:8443/ws/v5/public (<12ms)")
    print("  ⚡ Hyperliquid WS:   wss://api.hyperliquid.xyz/ws (<15ms push)")
    print("  ⚡ Polymarket WS:    wss://ws.perpetuals.polymarket.com/v1/ws (<20ms push)")
    print("  🎯 Lighter.xyz DEX:   wss://mainnet.zklighter.elliot.ai/stream (read-only target feed)")
    print("=" * 85)
    print("  Controls:")
    print(
        "  Press Ctrl+C once for graceful shutdown "
        f"({UVICORN_GRACEFUL_SHUTDOWN_SECONDS}s for HTTP/SSE clients, then "
        f"up to {GRACEFUL_SHUTDOWN_SECONDS}s to drain persistence queue)."
    )
    print("  A second Ctrl+C asks Uvicorn to force exit.")


    print("=" * 85)
    print()

    # Configure Uvicorn Server instance
    config = uvicorn.Config(
        "app.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=UVICORN_GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = GracefulUvicornServer(config)

    # Uvicorn owns SIGINT/SIGTERM. Its native handler runs the FastAPI lifespan;
    # GracefulUvicornServer only releases long-lived SSE clients early.
    try:
        server.run()
    except KeyboardInterrupt:
        # Uvicorn normally consumes the first Ctrl+C; retain a clean fallback for
        # terminals that raise KeyboardInterrupt before its handler is installed.
        server.should_exit = True
    finally:
        print("\n" + "=" * 85)
        print("  [OK] Server exited after the graceful shutdown lifecycle. Goodbye!")
        print("=" * 85)


if __name__ == "__main__":
    main()
