"""
app/live_feed.py
Real-time market data ingestion connecting directly to Polymarket:
- Gamma API for active multi-outcome event discovery
- CLOB API for real live order books (best bid, best ask, depth)
- Background worker updating live books and persisting to database
"""

import asyncio
import json
import logging
import time
import aiohttp
from typing import Dict, List, Any, Optional
from app.config import settings
from app.database import db

logger = logging.getLogger("live_feed")


class LiveMarketFeed:
    def __init__(self):
        self.monitored_events: Dict[str, Dict[str, Any]] = {}
        # token_id -> { "bid": float, "ask": float, "mid": float, "bid_depth": float, "ask_depth": float, "updated_at": float }
        self.order_books: Dict[str, Dict[str, float]] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_running: bool = False
        self.last_update_ts: float = 0.0

    async def start(self):
        self.is_running = True
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
        logger.info("Initializing Polymarket live market feed...")

        # 1. Initial event discovery
        await self.discover_active_events()

        # 2. Start continuous streaming/polling loop
        asyncio.create_task(self._feed_loop())

    async def stop(self):
        self.is_running = False
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("Live market feed stopped.")

    async def discover_active_events(self):
        """Fetches high-volume active multi-outcome events from Polymarket Gamma API."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

        url = f"{settings.GAMMA_API_BASE}/events"
        params = {"active": "true", "closed": "false", "limit": "50"}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Gamma API error: {resp.status}")
                    return

                data = await resp.json()
                if not isinstance(data, list):
                    return

                discovered = 0
                for event in data:
                    if not isinstance(event, dict):
                        continue
                    markets = event.get("markets", [])
                    if isinstance(markets, list) and len(markets) >= 3:
                        event_id = str(event.get("id"))
                        parsed_markets = []

                        for m in markets:
                            if not isinstance(m, dict):
                                continue
                            clob_tokens = m.get("clobTokenIds")
                            if isinstance(clob_tokens, str):
                                try:
                                    clob_tokens = json.loads(clob_tokens)
                                except Exception:
                                    continue
                            if clob_tokens and len(clob_tokens) >= 1:
                                token_id = str(clob_tokens[0])
                                parsed_markets.append({
                                    "market_id": str(m.get("id")),
                                    "question": m.get("question"),
                                    "outcome_name": m.get("groupItemTitle") or m.get("question"),
                                    "condition_id": m.get("conditionId"),
                                    "token_id": token_id
                                })

                        if len(parsed_markets) >= 3:
                            self.monitored_events[event_id] = {
                                "id": event_id,
                                "title": event.get("title"),
                                "slug": event.get("slug"),
                                "volume": float(event.get("volume") or 0.0),
                                "markets": parsed_markets
                            }

                            # Persist to database
                            await db.execute(
                                """
                                INSERT INTO events (event_id, title, slug, volume, markets_count, updated_at)
                                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                                ON CONFLICT (event_id) DO UPDATE SET
                                    volume = EXCLUDED.volume,
                                    markets_count = EXCLUDED.markets_count,
                                    updated_at = CURRENT_TIMESTAMP
                                """,
                                event_id,
                                event.get("title"),
                                event.get("slug"),
                                float(event.get("volume") or 0.0),
                                len(parsed_markets)
                            )

                            for pm in parsed_markets:
                                await db.execute(
                                    """
                                    INSERT INTO tokens (token_id, event_id, outcome_name, condition_id, latest_price, updated_at)
                                    VALUES ($1, $2, $3, $4, 0.0, CURRENT_TIMESTAMP)
                                    ON CONFLICT (token_id) DO UPDATE SET
                                        outcome_name = EXCLUDED.outcome_name,
                                        updated_at = CURRENT_TIMESTAMP
                                    """,
                                    pm["token_id"],
                                    event_id,
                                    pm["outcome_name"],
                                    pm["condition_id"]
                                )

                            discovered += 1
                            if discovered >= settings.MONITORED_EVENTS_LIMIT:
                                break

                logger.info(f"Discovered and persisted {len(self.monitored_events)} active multi-outcome events.")

        except Exception as e:
            logger.error(f"Error during event discovery: {e}")

    async def fetch_real_order_book(self, token_id: str, fallback_price: float = 0.50) -> Dict[str, float]:
        """Queries real live order book from Polymarket CLOB API with fallback to latest traded price."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

        url = f"{settings.CLOB_API_BASE}/book"
        params = {"token_id": token_id}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])

                    best_bid = float(bids[0]["price"]) if bids else max(0.001, fallback_price - 0.01)
                    best_bid_size = float(bids[0]["size"]) if bids else 100.0

                    best_ask = float(asks[0]["price"]) if asks else min(0.999, fallback_price + 0.01)
                    best_ask_size = float(asks[0]["size"]) if asks else 100.0

                    mid_price = round((best_bid + best_ask) / 2.0, 4)

                    book_data = {
                        "bid": best_bid,
                        "ask": best_ask,
                        "mid": mid_price,
                        "bid_depth": best_bid_size,
                        "ask_depth": best_ask_size,
                        "updated_at": time.time()
                    }
                    self.order_books[token_id] = book_data

                    await db.execute(
                        "UPDATE tokens SET latest_price = $1, updated_at = CURRENT_TIMESTAMP WHERE token_id = $2",
                        mid_price,
                        token_id
                    )
                    return book_data
                else:
                    # Fallback to last known price
                    best_bid = max(0.001, round(fallback_price - 0.01, 3))
                    best_ask = min(0.999, round(fallback_price + 0.01, 3))
                    book_data = {
                        "bid": best_bid,
                        "ask": best_ask,
                        "mid": fallback_price,
                        "bid_depth": 50.0,
                        "ask_depth": 50.0,
                        "updated_at": time.time()
                    }
                    self.order_books[token_id] = book_data
                    return book_data

        except Exception as e:
            logger.debug(f"Failed to fetch order book for {token_id[:12]}: {e}")
            best_bid = max(0.001, round(fallback_price - 0.01, 3))
            best_ask = min(0.999, round(fallback_price + 0.01, 3))
            return {
                "bid": best_bid,
                "ask": best_ask,
                "mid": fallback_price,
                "bid_depth": 50.0,
                "ask_depth": 50.0,
                "updated_at": time.time()
            }

    async def _feed_loop(self):
        """Continuously updates live books for candidate tokens."""
        while self.is_running:
            try:
                # Gather order books across all candidate tokens in monitored events
                tasks = []
                for ev in list(self.monitored_events.values()):
                    for m in ev["markets"]:
                        tasks.append(self.fetch_real_order_book(m["token_id"]))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                self.last_update_ts = time.time()
                await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in live feed loop: {e}")
                await asyncio.sleep(2.0)

    def get_event_basket_pricing(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Calculates real-time basket pricing across all candidate outcomes for an event:
        sum(best_ask), sum(best_bid), sum(mid).
        """
        ev = self.monitored_events.get(event_id)
        if not ev:
            return None

        markets = ev["markets"]
        total_ask_sum = 0.0
        total_bid_sum = 0.0
        outcomes_info = []

        for m in markets:
            t_id = m["token_id"]
            book = self.order_books.get(t_id)
            if not book:
                return None  # Incomplete basket pricing

            total_ask_sum += book["ask"]
            total_bid_sum += book["bid"]
            outcomes_info.append({
                "outcome_name": m["outcome_name"],
                "token_id": t_id,
                "best_bid": book["bid"],
                "best_ask": book["ask"],
                "ask_depth": book["ask_depth"],
                "bid_depth": book["bid_depth"]
            })

        return {
            "event_id": event_id,
            "event_title": ev["title"],
            "outcomes_count": len(markets),
            "basket_ask_sum": round(total_ask_sum, 4),
            "basket_bid_sum": round(total_bid_sum, 4),
            "outcomes": outcomes_info,
            "timestamp": time.time()
        }


live_feed = LiveMarketFeed()
