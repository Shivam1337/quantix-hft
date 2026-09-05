"""
test_system.py
Unit and integration test verifying:
1. Database connectivity and schema creation
2. Read-only SQL query validation & security guards
3. Live Polymarket order book ingestion
4. Real fee calculations
5. Simulated trade execution with $50 capital limit
"""

import asyncio
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database import db
from app.fee_model import real_fees
from app.query_service import execute_read_only_query, validate_read_only_query
from app.live_feed import live_feed
from app.simulator import simulator
from app.arb_engine import arb_engine


async def test_all():
    print("=" * 60)
    print("RUNNING POLYMARKET REAL-TIME ENGINE INTEGRATION TESTS")
    print("=" * 60)

    # 1. Database initialization
    print("\n1. Testing Database Initialization...")
    await db.initialize()
    print(f"   [PASS] DB initialized ({'PostgreSQL' if db.is_postgres else 'SQLite fallback'}).")

    # 2. Test Real Fee Model
    print("\n2. Testing Real Fee Model (Polygon L2 & Polymarket CLOB)...")
    gas_cost = real_fees.calculate_polygon_gas_usd(is_multi_token_basket=True)
    print(f"   Real Polygon Basket Gas Cost: ${gas_cost:.4f} USD")
    assert 0.001 <= gas_cost <= 0.05, f"Unexpected gas cost: {gas_cost}"

    friction_info = real_fees.calculate_effective_execution_cost(
        notional_usd=25.0,
        is_taker=True,
        event_title="Will Trump win 2028?",
        is_basket=True
    )
    print(f"   Total Friction on $25 notional: ${friction_info['total_friction_usd']:.4f} ({friction_info['friction_pct']}%)")
    print("   [PASS] Real fee model verified.")

    # 3. Test Read-Only Query Security Guards
    print("\n3. Testing Read-Only Query Security Guards...")
    # Valid SELECT
    res = await execute_read_only_query("SELECT 1 as test_val;")
    assert res["success"] is True, f"Valid query failed: {res}"
    print("   [PASS] Valid SELECT executed successfully.")

    # Dangerous DROP query
    res_drop = await execute_read_only_query("DROP TABLE events;")
    assert res_drop["success"] is False, "Security failed: DROP TABLE was not blocked!"
    print(f"   [PASS] DROP TABLE blocked: '{res_drop.get('error')}'")

    # Dangerous INSERT query
    res_ins = await execute_read_only_query("INSERT INTO events (event_id, title) VALUES ('1', 'bad');")
    assert res_ins["success"] is False, "Security failed: INSERT was not blocked!"
    print(f"   [PASS] INSERT blocked: '{res_ins.get('error')}'")

    # 4. Test Live Polymarket Feed Ingestion
    print("\n4. Testing Live Polymarket Feed Ingestion...")
    await live_feed.discover_active_events()
    assert len(live_feed.monitored_events) > 0, "No active events discovered from Gamma API!"
    sample_ev = list(live_feed.monitored_events.values())[0]
    print(f"   [PASS] Discovered {len(live_feed.monitored_events)} events. Sample: '{sample_ev['title']}'")

    sample_token = sample_ev["markets"][0]["token_id"]
    book = await live_feed.fetch_real_order_book(sample_token)
    assert book is not None, f"Failed to fetch real order book for token {sample_token}"
    bid_str = f"${book['bid']:.3f}" if book['bid'] is not None else "None"
    ask_str = f"${book['ask']:.3f}" if book['ask'] is not None else "None"
    mid_str = f"${book['mid']:.3f}" if book['mid'] is not None else "None"
    print(f"   [PASS] Fetched real order book: Bid={bid_str}, Ask={ask_str}, Mid={mid_str}")

    # 5. Test Simulated Execution with $50 Bankroll
    print("\n5. Testing Virtual Portfolio & Simulated Execution ($50 Limit)...")
    await simulator.initialize()
    await simulator.reset_simulation()
    summary = simulator.get_portfolio_summary()
    assert summary["cash"] == 50.0, f"Expected initial cash $50.00, got ${summary['cash']}"
    print(f"   Initial Cash: ${summary['cash']:.2f} | Locked: ${summary['locked_capital']:.2f}")

    # Simulate an entry on a sample basket
    mock_pricing = {
        "event_id": sample_ev["id"],
        "event_title": sample_ev["title"],
        "outcomes_count": len(sample_ev["markets"]),
        "basket_ask_sum": 0.940, # Synthetic mispricing
        "basket_bid_sum": 0.920,
        "outcomes": [
            {"token_id": m["token_id"], "outcome_name": m["outcome_name"], "best_ask": 0.31, "best_bid": 0.30}
            for m in sample_ev["markets"]
        ]
    }
    executed = await simulator.execute_simulated_basket_entry(
        event_id=sample_ev["id"],
        event_title=sample_ev["title"],
        basket_pricing=mock_pricing,
        net_spread=0.045
    )
    assert executed is True, "Simulated basket entry execution failed"
    after_trade = simulator.get_portfolio_summary()
    assert after_trade["cash"] < 50.0, "Cash was not deducted"
    assert after_trade["locked_capital"] > 0, "Locked capital was not updated"
    assert after_trade["open_positions_count"] == 1, "Position count mismatch"
    print(f"   After Trade -> Cash: ${after_trade['cash']:.2f}, Locked: ${after_trade['locked_capital']:.2f}, Total Equity: ${after_trade['total_equity']:.2f}")
    print("   [PASS] Virtual execution and position tracking verified.")

    # 6. Test Querying DB for newly created records
    print("\n6. Testing SQL Query Runner on Live DB Records...")
    query_res = await execute_read_only_query("SELECT id, event_title, position_type, cost, status FROM simulated_positions;")
    assert query_res["success"] is True and query_res["row_count"] >= 1
    print(f"   [PASS] Query returned {query_res['row_count']} row(s): {query_res['rows'][0]}")

    # 7. Test State Continuity Across Re-initialization (CD safety)
    print("\n7. Testing State Continuity Across Redeployment Re-initialization...")
    # Re-initialize simulator as if a new container just booted
    await simulator.initialize()
    reloaded_summary = simulator.get_portfolio_summary()
    assert reloaded_summary["open_positions_count"] == 1, "Open positions failed to persist across re-initialization"
    assert reloaded_summary["cash"] == after_trade["cash"], "Cash balance changed across re-initialization"
    assert simulator.position_counter >= 1, "Position counter was not restored"
    print(f"   [PASS] State continuity verified: {reloaded_summary['open_positions_count']} open position(s) restored.")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
    await live_feed.stop()
    await db.close()


if __name__ == "__main__":
    asyncio.run(test_all())
