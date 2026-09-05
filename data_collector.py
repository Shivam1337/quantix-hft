"""
Real-World Tick & L2 Order Book Data Collector for Hyperliquid.
Streams and records synchronized L2 order book snapshots/updates and executed trades.
"""

import asyncio
import json
import time
import sys
from typing import Optional
import websockets


class HyperliquidDataCollector:
    """
    Subscribes to Hyperliquid's WebSocket feed and saves raw L2 book states and trades.
    """

    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(self, coin: str, output_file: str):
        self.coin = coin
        self.output_file = output_file
        self.event_count = 0
        self.trade_count = 0
        self.book_count = 0

    async def collect(self, duration_seconds: int = 60, max_events: Optional[int] = None):
        print(f"Connecting to {self.WS_URL} to collect data for {self.coin}...")
        start_time = time.time()
        retry_delay = 2.0

        with open(self.output_file, "a", encoding="utf-8") as f:
            while True:
                elapsed = time.time() - start_time
                if duration_seconds and elapsed >= duration_seconds:
                    break
                if max_events and self.event_count >= max_events:
                    break

                try:
                    async with websockets.connect(self.WS_URL, ping_interval=20, ping_timeout=10) as ws:
                        retry_delay = 2.0  # Reset on successful connect
                        # Subscribe to L2 orderbook
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "l2Book", "coin": self.coin}
                        }))

                        # Subscribe to trades
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "trades", "coin": self.coin}
                        }))

                        print(f"Connected to {self.coin} feed. Recording...")

                        while True:
                            elapsed = time.time() - start_time
                            if duration_seconds and elapsed >= duration_seconds:
                                break
                            if max_events and self.event_count >= max_events:
                                break

                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(msg)
                        channel = data.get("channel")

                        if channel in ("l2Book", "trades"):
                            payload = {
                                "local_time": time.time(),
                                "channel": channel,
                                "data": data.get("data")
                            }
                            f.write(json.dumps(payload) + "\n")
                            self.event_count += 1

                            if channel == "l2Book":
                                self.book_count += 1
                            elif channel == "trades":
                                num_trades = len(data.get("data", []))
                                self.trade_count += num_trades

                            if self.event_count % 100 == 0:
                                print(f"[{elapsed:.1f}s] Recorded {self.event_count} events ({self.book_count} books, {self.trade_count} trades)")

                    except asyncio.TimeoutError:
                        continue
                except Exception as e:
                    print(f"Connection error: {e}. Retrying in {retry_delay:.1f}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 15.0)

        print(f"Collection complete: Total {self.event_count} events ({self.book_count} books, {self.trade_count} trades) saved to {self.output_file}.")


if __name__ == "__main__":
    coin = sys.argv[1] if len(sys.argv) > 1 else "HYPE"
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 45
    output = f"tick_data_{coin.lower()}.jsonl"
    asyncio.run(HyperliquidDataCollector(coin, output).collect(duration_seconds=duration))
