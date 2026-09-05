"""
High-Frequency Market Making Backtest Orchestrator & Analyzer.
Loads real-world tick data, benchmarks 3 strategies (Naive, AS, AS+OFI),
calculates statistical metrics, and generates comparison performance charts.
"""

import json
import sys
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from signals import BookLevel, OrderBook, AvellanedaStoikovModel
from engine import HFMarketMakingSimulator


def load_tick_data(filepath: str):
    """Loads and chronologically sorts raw WebSocket events."""
    events = []
    print(f"Loading tick data from {filepath}...")
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            event = json.loads(line)
            events.append(event)
    print(f"Loaded {len(events)} events.")
    return events


def run_benchmark(events, coin: str, tick_size: float = 0.001, initial_capital: float = 1000.0):
    """Runs Naive, AS, and AS+OFI strategies on the identical event stream."""

    # Infer reasonable parameters based on coin price
    first_book = None
    for e in events:
        if e.get("channel") == "l2Book" and e.get("data"):
            first_book = e["data"]
            break

    if not first_book:
        raise ValueError("No valid l2Book events found in dataset.")

    sample_px = float(first_book["levels"][0][0]["px"])
    order_size_usd = 50.0  # $50 quotes per side

    # Determine tick size dynamically if needed
    if sample_px > 1000:
        tick_sz = 0.1
    elif sample_px > 10:
        tick_sz = 0.001
    elif sample_px > 1:
        tick_sz = 0.0001
    else:
        tick_sz = 0.00001

    print(f"Sample price for {coin}: ${sample_px:.4f}, using tick size {tick_sz}")

    # Initialize models
    model_naive = AvellanedaStoikovModel(gamma=0.0, kappa=1.5, beta_ofi=0.0, tick_size=tick_sz, min_spread_bps=1.5, max_inventory_usd=250.0)
    model_as = AvellanedaStoikovModel(gamma=0.5, kappa=1.5, beta_ofi=0.0, tick_size=tick_sz, min_spread_bps=1.5, max_inventory_usd=250.0)
    model_as_ofi = AvellanedaStoikovModel(gamma=0.5, kappa=1.5, beta_ofi=0.6, tick_size=tick_sz, min_spread_bps=1.5, max_inventory_usd=250.0)

    sim_naive = HFMarketMakingSimulator(
        strategy_name="Naive Fixed-Spread",
        model=model_naive,
        initial_capital=initial_capital,
        order_size_notional=order_size_usd,
        maker_fee_rate=0.0001,  # 0.01%
        latency_ms=10.0,
        max_inventory_notional=250.0,
        use_inventory_skew=False,
        use_ofi=False
    )

    sim_as = HFMarketMakingSimulator(
        strategy_name="Classical AS (Inventory Skew)",
        model=model_as,
        initial_capital=initial_capital,
        order_size_notional=order_size_usd,
        maker_fee_rate=0.0001,
        latency_ms=10.0,
        max_inventory_notional=250.0,
        use_inventory_skew=True,
        use_ofi=False
    )

    sim_as_ofi = HFMarketMakingSimulator(
        strategy_name="AS + OFI Enhanced",
        model=model_as_ofi,
        initial_capital=initial_capital,
        order_size_notional=order_size_usd,
        maker_fee_rate=0.0001,
        latency_ms=10.0,
        max_inventory_notional=250.0,
        use_inventory_skew=True,
        use_ofi=True
    )

    simulators = [sim_naive, sim_as, sim_as_ofi]

    # Replay tick stream
    print("Replaying tick stream across strategies...")
    for idx, e in enumerate(events):
        channel = e.get("channel")
        data = e.get("data")
        local_time = e.get("local_time", 0.0)

        if channel == "l2Book" and data:
            raw_bids = data.get("levels", [[], []])[0]
            raw_asks = data.get("levels", [[], []])[1]
            if not raw_bids or not raw_asks:
                continue

            bids = [BookLevel(price=float(b["px"]), size=float(b["sz"]), orders=int(b.get("n", 1))) for b in raw_bids]
            asks = [BookLevel(price=float(a["px"]), size=float(a["sz"]), orders=int(a.get("n", 1))) for a in raw_asks]

            book = OrderBook(bids=bids, asks=asks, timestamp=local_time)

            for sim in simulators:
                sim.on_book_update(book)

        elif channel == "trades" and data:
            for trade in data:
                t_px = float(trade["px"])
                t_sz = float(trade["sz"])
                t_side = trade["side"] # 'B' or 'A'
                t_time = trade.get("time", local_time * 1000) / 1000.0

                for sim in simulators:
                    sim.on_trade(timestamp=t_time, price=t_px, size=t_sz, side=t_side)

    # Collect statistics
    stats_list = [sim.get_stats() for sim in simulators]
    df_stats = pd.DataFrame(stats_list)

    print("\n" + "=" * 80)
    print(f"BACKTEST RESULTS SUMMARY ({coin} Real-World Tick Data)")
    print("=" * 80)
    print(df_stats.to_string(index=False))
    print("=" * 80 + "\n")

    # Generate charts
    plot_results(simulators, coin)

    return df_stats, simulators


def plot_results(simulators, coin: str, output_image: str = "backtest_results.png"):
    """Plots comparative equity curves, drawdowns, and inventory trajectories."""
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    colors = ["#e74c3c", "#3498db", "#2ecc71"] # Red, Blue, Green

    # Plot 1: Cumulative PnL ($)
    ax1 = axes[0]
    for sim, color in zip(simulators, colors):
        if not sim.equity_history:
            continue
        times = [e[0] - sim.equity_history[0][0] for e in sim.equity_history]
        pnls = [e[1] - sim.initial_capital for e in sim.equity_history]
        ax1.plot(times, pnls, label=f"{sim.strategy_name} (PnL: ${pnls[-1]:+.2f})", color=color, linewidth=1.8)

    ax1.set_title(f"Cumulative P&L ($) - Real Market Data ({coin})", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Net P&L ($)")
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend(loc="upper left")

    # Plot 2: Inventory Evolution (q)
    ax2 = axes[1]
    for sim, color in zip(simulators, colors):
        if not sim.equity_history:
            continue
        times = [e[0] - sim.equity_history[0][0] for e in sim.equity_history]
        inv = [e[2] for e in sim.equity_history]
        ax2.plot(times, inv, label=f"{sim.strategy_name} (Std: {np.std(inv):.2f})", color=color, linewidth=1.2)

    ax2.axhline(0, color="black", linestyle=":", alpha=0.7)
    ax2.set_title("Inventory Position (Contracts) - Risk Accumulation vs Mean Reversion", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Inventory (Contracts)")
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper left")

    # Plot 3: Underlying Asset Mid-Price
    ax3 = axes[2]
    if simulators[0].equity_history:
        times = [e[0] - simulators[0].equity_history[0][0] for e in simulators[0].equity_history]
        mids = [e[3] for e in simulators[0].equity_history]
        ax3.plot(times, mids, color="#34495e", linewidth=1.2, label=f"{coin} Mid-Price")

    ax3.set_title(f"Underlying Asset Mid-Price ({coin})", fontsize=12, fontweight="bold")
    ax3.set_xlabel("Elapsed Time (Seconds)")
    ax3.set_ylabel("Price ($)")
    ax3.grid(True, linestyle="--", alpha=0.5)
    ax3.legend(loc="upper left")

    plt.tight_layout()
    plt.savefig(output_image, dpi=180)
    print(f"Saved performance chart to {output_image}")
    plt.close()


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "tick_data_hype.jsonl"
    coin = sys.argv[2] if len(sys.argv) > 2 else "HYPE"
    if os.path.exists(filepath):
        run_benchmark(load_tick_data(filepath), coin)
    else:
        print(f"File {filepath} does not exist.")
