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
        self._feed_task = asyncio.create_task(self._feed_loop())

    async def stop(self):
        self.is_running = False
        if hasattr(self, "_feed_task") and self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("Live market feed stopped.")

    async def discover_active_events(self):
        """Fetches high-volume active NegRisk and combinatorial multi-outcome events from Polymarket Gamma API."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

        url = f"{settings.GAMMA_API_BASE}/events"
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": "100"
        }
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
                    if not isinstance(markets, list):
                        continue

                    # Filter for true combinatorial / NegRisk events:
                    # 1. Flagged as NegRisk (mutually exclusive outcomes where sum(P) = 1.0)
                    # 2. 3-way match outcomes (Home, Draw, Away)
                    # 3. High volume binary markets (YES + NO pair where sum(P) = 1.0)
                    is_neg_risk = any(m.get("negRisk") is True for m in markets)
                    is_three_way = (
                        len(markets) == 3 and
                        any("win" in m.get("question", "").lower() or "draw" in m.get("question", "").lower() for m in markets)
                    )

                    event_id = str(event.get("id"))
                    parsed_markets = []

                    if (is_neg_risk or is_three_way) and 2 <= len(markets) <= 12:
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
                                # Extract real prices from Gamma market metadata
                                ask_val = float(m["bestAsk"]) if m.get("bestAsk") is not None else None
                                bid_val = float(m["bestBid"]) if m.get("bestBid") is not None else None
                                parsed_markets.append({
                                    "market_id": str(m.get("id")),
                                    "question": m.get("question"),
                                    "outcome_name": m.get("groupItemTitle") or m.get("question"),
                                    "condition_id": m.get("conditionId"),
                                    "token_id": token_id,
                                    "initial_ask": ask_val,
                                    "initial_bid": bid_val
                                })

                    # Also support single-market binary events (YES and NO token pair)
                    elif len(markets) == 1:
                        m = markets[0]
                        clob_tokens = m.get("clobTokenIds")
                        if isinstance(clob_tokens, str):
                            try:
                                clob_tokens = json.loads(clob_tokens)
                            except Exception:
                                clob_tokens = None
                        if clob_tokens and len(clob_tokens) == 2:
                            yes_token = str(clob_tokens[0])
                            no_token = str(clob_tokens[1])
                            yes_ask = float(m["bestAsk"]) if m.get("bestAsk") is not None else None
                            yes_bid = float(m["bestBid"]) if m.get("bestBid") is not None else None
                            if yes_ask is not None:
                                no_ask = round(1.0 - (yes_bid if yes_bid is not None else (yes_ask - 0.02)), 4)
                                no_bid = round(1.0 - yes_ask, 4)
                                parsed_markets.append({
                                    "market_id": str(m.get("id")),
                                    "question": m.get("question"),
                                    "outcome_name": "YES",
                                    "condition_id": m.get("conditionId"),
                                    "token_id": yes_token,
                                    "initial_ask": yes_ask,
                                    "initial_bid": yes_bid
                                })
                                parsed_markets.append({
                                    "market_id": str(m.get("id")),
                                    "question": m.get("question"),
                                    "outcome_name": "NO",
                                    "condition_id": m.get("conditionId"),
                                    "token_id": no_token,
                                    "initial_ask": no_ask,
                                    "initial_bid": no_bid
                                })

                    if len(parsed_markets) >= 2:
                        self.monitored_events[event_id] = {
                            "id": event_id,
                            "title": event.get("title") or event.get("question"),
                            "slug": event.get("slug"),
                            "volume": float(event.get("volume") or 0.0),
                            "markets": parsed_markets
                        }

                        # Seed initial order books with real Gamma prices
                        for pm in parsed_markets:
                            if pm["initial_ask"] is not None:
                                init_ask = pm["initial_ask"]
                                init_bid = pm["initial_bid"] or max(0.001, round(init_ask - 0.01, 3))
                                self.order_books[pm["token_id"]] = {
                                    "bid": init_bid,
                                    "ask": init_ask,
                                    "mid": round((init_bid + init_ask) / 2.0, 4),
                                    "bid_depth": 100.0,
                                    "ask_depth": 100.0,
                                    "updated_at": time.time()
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
                            event.get("title") or event.get("question"),
                            event.get("slug"),
                            float(event.get("volume") or 0.0),
                            len(parsed_markets)
                        )

                        for pm in parsed_markets:
                            latest_p = pm["initial_ask"] or 0.0
                            await db.execute(
                                """
                                INSERT INTO tokens (token_id, event_id, outcome_name, condition_id, latest_price, updated_at)
                                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                                ON CONFLICT (token_id) DO UPDATE SET
                                    outcome_name = EXCLUDED.outcome_name,
                                    latest_price = EXCLUDED.latest_price,
                                    updated_at = CURRENT_TIMESTAMP
                                """,
                                pm["token_id"],
                                event_id,
                                pm["outcome_name"],
                                pm["condition_id"],
                                latest_p
                            )

                        discovered += 1
                        if discovered >= settings.MONITORED_EVENTS_LIMIT:
                            break

                logger.info(f"Discovered and persisted {len(self.monitored_events)} active combinatorial events.")

        except Exception as e:
            logger.error(f"Error during event discovery: {e}")

    async def fetch_real_order_book(self, token_id: str, fallback_price: Optional[float] = None) -> Dict[str, float]:
        """Queries real live order book from Polymarket CLOB API with fallback to latest known market price."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

        existing = self.order_books.get(token_id, {})
        default_ask = existing.get("ask", fallback_price if fallback_price is not None else 0.50)
        default_bid = existing.get("bid", max(0.001, round(default_ask - 0.01, 3)))

        url = f"{settings.CLOB_API_BASE}/book"
        params = {"token_id": token_id}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])

                    best_bid = float(bids[0]["price"]) if bids else default_bid
                    best_bid_size = float(bids[0]["size"]) if bids else 100.0

                    best_ask = float(asks[0]["price"]) if asks else default_ask
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
                    return existing or {
                        "bid": default_bid,
                        "ask": default_ask,
                        "mid": round((default_bid + default_ask) / 2.0, 4),
                        "bid_depth": 50.0,
                        "ask_depth": 50.0,
                        "updated_at": time.time()
                    }

        except Exception as e:
            logger.debug(f"Failed to fetch order book for {token_id[:12]}: {e}")
            return existing or {
                "bid": default_bid,
                "ask": default_ask,
                "mid": round((default_bid + default_ask) / 2.0, 4),
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
