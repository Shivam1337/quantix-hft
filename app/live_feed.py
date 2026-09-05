"""
app/live_feed.py
Real-time market data ingestion connecting directly to Polymarket:
- Gamma API for active multi-outcome event discovery
- CLOB API for real live order books (best bid, best ask, depth)
- Background worker updating live books and persisting to database
"""

import asyncio
from datetime import datetime, timezone
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
        """
        Fetches high-volume active NegRisk and combinatorial events from Polymarket Gamma API,
        specifically targeting and prioritizing high-moving crypto markets (BTC, ETH, SOL, XRP, etc.)
        alongside top macro/sports events.
        """
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

        crypto_keywords = {
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
            "xrp", "binance", "coinbase", "doge", "pepe", "tether", "usdt"
        }

        try:
            # Concurrently query dedicated crypto events and top general volume events
            crypto_task = self.session.get(
                f"{settings.GAMMA_API_BASE}/events",
                params={"active": "true", "closed": "false", "tag_slug": "crypto", "order": "volume24hr", "ascending": "false", "limit": "100"}
            )
            general_task = self.session.get(
                f"{settings.GAMMA_API_BASE}/events",
                params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": "100"}
            )
            resps = await asyncio.gather(crypto_task, general_task, return_exceptions=True)

            merged_events = {}

            # 1. Process dedicated crypto events
            if not isinstance(resps[0], Exception) and resps[0].status == 200:
                c_data = await resps[0].json()
                if isinstance(c_data, list):
                    for e in c_data:
                        if isinstance(e, dict):
                            e["is_crypto"] = True
                            merged_events[str(e.get("id"))] = e

            # 2. Process general high-volume events
            if not isinstance(resps[1], Exception) and resps[1].status == 200:
                g_data = await resps[1].json()
                if isinstance(g_data, list):
                    for e in g_data:
                        if isinstance(e, dict):
                            eid = str(e.get("id"))
                            if eid not in merged_events:
                                t_low = (e.get("title") or "").lower()
                                e["is_crypto"] = any(k in t_low for k in crypto_keywords)
                                merged_events[eid] = e

            # Filter candidates for valid active combinatorial baskets
            candidate_events = []
            now_utc = datetime.now(timezone.utc)
            for event in merged_events.values():
                if event.get("closed") is True or event.get("active") is False:
                    continue

                # Filter out expired events
                end_str = event.get("endDate")
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt <= now_utc:
                            continue
                    except Exception:
                        pass

                markets = event.get("markets", [])
                if not isinstance(markets, list) or len(markets) == 0:
                    continue

                is_neg_risk = any(m.get("negRisk") is True for m in markets)
                is_three_way = (
                    len(markets) == 3 and
                    any("win" in m.get("question", "").lower() or "draw" in m.get("question", "").lower() for m in markets)
                )

                # Multi-bracket NegRisk / 3-way match
                if (is_neg_risk or is_three_way) and 2 <= len(markets) <= 15:
                    candidate_events.append(event)
                # Or binary event with YES + NO tokens
                elif len(markets) == 1:
                    m = markets[0]
                    clob_tokens = m.get("clobTokenIds")
                    if isinstance(clob_tokens, str):
                        try:
                            clob_tokens = json.loads(clob_tokens)
                        except Exception:
                            clob_tokens = None
                    if clob_tokens and len(clob_tokens) == 2:
                        candidate_events.append(event)

            # Prioritize fast-moving crypto events, then high volume
            candidate_events.sort(
                key=lambda x: (x.get("is_crypto", False), float(x.get("volume", 0) or 0.0)),
                reverse=True
            )

            discovered = 0
            for event in candidate_events:
                event_id = str(event.get("id"))
                markets = event.get("markets", [])
                is_crypto = event.get("is_crypto", False)
                parsed_markets = []

                if len(markets) >= 2:
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
                                "token_id": token_id,
                                "initial_ask": float(m["bestAsk"]) if m.get("bestAsk") is not None else None,
                                "initial_bid": float(m["bestBid"]) if m.get("bestBid") is not None else None
                            })
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
                        # In binary markets, both YES and NO tokens must be fetched directly from CLOB
                        parsed_markets.append({
                            "market_id": str(m.get("id")),
                            "question": m.get("question"),
                            "outcome_name": "YES",
                            "condition_id": m.get("conditionId"),
                            "token_id": yes_token,
                            "initial_ask": float(m["bestAsk"]) if m.get("bestAsk") is not None else None,
                            "initial_bid": float(m["bestBid"]) if m.get("bestBid") is not None else None
                        })
                        parsed_markets.append({
                            "market_id": str(m.get("id")),
                            "question": m.get("question"),
                            "outcome_name": "NO",
                            "condition_id": m.get("conditionId"),
                            "token_id": no_token,
                            "initial_ask": None,
                            "initial_bid": None
                        })

                if len(parsed_markets) >= 2:
                    self.monitored_events[event_id] = {
                        "id": event_id,
                        "title": event.get("title") or event.get("question"),
                        "slug": event.get("slug"),
                        "volume": float(event.get("volume") or 0.0),
                        "is_crypto": is_crypto,
                        "markets": parsed_markets
                    }

                    # Persist to database
                    await db.execute(
                        """
                        INSERT INTO events (event_id, title, slug, volume, markets_count, updated_at)
                        VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                        ON CONFLICT (event_id) DO UPDATE SET
                            title = EXCLUDED.title,
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

            crypto_count = sum(1 for e in self.monitored_events.values() if e.get("is_crypto"))
            logger.info(f"Discovered and persisted {len(self.monitored_events)} events ({crypto_count} high-moving crypto).")

        except Exception as e:
            logger.error(f"Error during event discovery: {e}")

    async def fetch_real_order_book(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Queries real live order book from Polymarket CLOB API. Returns None if invalid or unavailable."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "Mozilla/5.0"}
            )

        url = f"{settings.CLOB_API_BASE}/book"
        params = {"token_id": token_id}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])

                    # Polymarket CLOB order books require:
                    # - Best Bid: HIGHEST bid price (max)
                    # - Best Ask: LOWEST ask price (min)
                    best_bid_entry = max(bids, key=lambda b: float(b["price"])) if bids else None
                    best_bid = float(best_bid_entry["price"]) if best_bid_entry else None
                    best_bid_size = float(best_bid_entry.get("size", 0.0)) if best_bid_entry else 0.0

                    best_ask_entry = min(asks, key=lambda a: float(a["price"])) if asks else None
                    best_ask = float(best_ask_entry["price"]) if best_ask_entry else None
                    best_ask_size = float(best_ask_entry.get("size", 0.0)) if best_ask_entry else 0.0

                    mid_price = None
                    if best_bid is not None and best_ask is not None:
                        mid_price = round((best_bid + best_ask) / 2.0, 4)
                    elif best_ask is not None:
                        mid_price = best_ask
                    elif best_bid is not None:
                        mid_price = best_bid

                    book_data = {
                        "bid": best_bid,
                        "ask": best_ask,
                        "mid": mid_price,
                        "bid_depth": best_bid_size,
                        "ask_depth": best_ask_size,
                        "updated_at": time.time()
                    }
                    self.order_books[token_id] = book_data

                    if mid_price is not None:
                        await db.execute(
                            "UPDATE tokens SET latest_price = $1, updated_at = CURRENT_TIMESTAMP WHERE token_id = $2",
                            mid_price,
                            token_id
                        )
                    return book_data
                else:
                    self.order_books[token_id] = None
                    return None

        except Exception as e:
            logger.debug(f"Failed to fetch order book for {token_id[:12]}: {e}")
            self.order_books[token_id] = None
            return None

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
        Calculates real-time basket pricing across all candidate outcomes for an event.
        Requires genuine, liquid CLOB order books for EVERY outcome.
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

            # Strictly require a real best ask and liquidity >= 5.0 to buy
            if book["ask"] is None or book["ask"] <= 0 or book["ask_depth"] < 5.0:
                return None

            best_ask = book["ask"]
            best_bid = book["bid"] if (book["bid"] is not None and book["bid_depth"] >= 5.0) else 0.0

            total_ask_sum += best_ask
            total_bid_sum += best_bid
            outcomes_info.append({
                "outcome_name": m["outcome_name"],
                "token_id": t_id,
                "best_bid": book["bid"],
                "best_ask": best_ask,
                "ask_depth": book["ask_depth"],
                "bid_depth": book["bid_depth"]
            })

        # Sanity check: In a real market, bid sum can never exceed ask sum
        # If bid sum >= ask sum or exceeds settlement cap (1.05), the books are crossed or anomalous
        if total_bid_sum >= total_ask_sum or total_bid_sum > 1.05:
            return None

        return {
            "event_id": event_id,
            "event_title": ev["title"],
            "outcomes_count": len(markets),
            "is_crypto": ev.get("is_crypto", False),
            "basket_ask_sum": round(total_ask_sum, 4),
            "basket_bid_sum": round(total_bid_sum, 4),
            "outcomes": outcomes_info,
            "timestamp": time.time()
        }


live_feed = LiveMarketFeed()
