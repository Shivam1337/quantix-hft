import requests

def screen():
    r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}).json()
    universe = r[0]["universe"]
    ctxs = r[1]

    results = []
    for u, c in zip(universe, ctxs):
        name = u["name"]
        vol = float(c.get("dayNtlVlm", 0))
        mark = float(c.get("markPx", 0))
        if vol > 500000 and mark > 0:
            results.append((name, vol, mark))

    results.sort(key=lambda x: x[1], reverse=True)

    print(f"{'Coin':<10} {'24h Vol (USD)':<16} {'Mark Price':<12} {'Spread (bps)':<12} {'Depth ($)':<12}")
    print("-" * 65)

    candidates = []
    for name, vol, mark in results[:35]:
        try:
            book = requests.post("https://api.hyperliquid.xyz/info", json={"type": "l2Book", "coin": name}, timeout=3).json()
            bids = book["levels"][0]
            asks = book["levels"][1]
            if bids and asks:
                bb = float(bids[0]["px"])
                ba = float(asks[0]["px"])
                spread_bps = (ba - bb) / bb * 10000
                top_depth = float(bids[0]["sz"]) * bb
                print(f"{name:<10} ${vol:>14,.0f} ${mark:<11.4f} {spread_bps:>8.2f} bps  ${top_depth:>9,.0f}")
                if spread_bps > 1.5 and vol > 1000000:
                    candidates.append((name, vol, spread_bps, top_depth))
        except Exception:
            pass

    print("\nBest candidates for small capital market making (High Vol + Wide Spread):")
    for c in candidates:
        print(f"  {c[0]}: Vol=${c[1]:,.0f}, Spread={c[2]:.2f} bps, TopDepth=${c[3]:,.0f}")

if __name__ == "__main__":
    screen()
