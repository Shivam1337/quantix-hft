# Quantix // High-Frequency Microstructure Market Making Engine

A high-frequency market making engine and control dashboard designed for small-capital quantitative traders ($500 – $5,000). The system exploits capacity-constrained long-tail order books on crypto perpetual exchanges (Hyperliquid) where tier-1 institutional firms cannot trade due to market impact.

---

## Core Architecture & Alpha Strategy

1. **Avellaneda-Stoikov Reservation Price:**
   $$r(s, q) = s \times \left(1 - q_{\text{norm}} \cdot \gamma \cdot \sigma\right)$$
   Penalizes holding inventory to prevent position accumulation and dynamically mean-reverts back to flat.

2. **Order Flow Imbalance (OFI) Skew:**
   $$\text{OFI}_t = \Delta Q_{\text{bid}} - \Delta Q_{\text{ask}}$$
   $$r^*(s, q) = r(s, q) + \beta_{\text{ofi}} \cdot \tanh\left(\frac{\text{OFI}_t}{50}\right) \cdot \frac{\text{Spread}}{2}$$
   Anticipates aggressive market sweeps 50ms–1s in advance and asymmetrically shifts or cancels vulnerable quotes.

3. **Protective Microstructure Circuit Breakers:**
   * **Spread Floor Gatekeeper ($4.5\text{ bps}$):** Automatically pulls all quotes to cash when market spread compresses below round-trip fee thresholds.
   * **Unilateral Inventory Offload:** Completely kills buy quotes once holding $> \$30$ of inventory and aggressively quotes asks at the top of the book.
   * **Waterfall Momentum Halt:** Freezes buy quotes if price drops $> 10\text{ bps}$ in 10 seconds or $\text{OFI} < -1200$.
   * **Emergency Taker Stop-Loss:** Market-liquidates positions if inventory drops $> 25\text{ bps}$ below weighted average entry price.

4. **Real Exchange Fee Modeling:**
   * Base Maker Fee: $0.015\%$ ($1.5\text{ bps}$)
   * Base Taker Fee: $0.045\%$ ($4.5\text{ bps}$)

5. **PostgreSQL Persistence Layer:**
   * Asynchronous logging of every trading session, execution fill, and tick telemetry snapshot via `asyncpg`.
   * CSV post-trade export functionality.

---

## Quick Start (Docker Deployment)

### 1. Run with Docker Compose
```bash
docker compose up --build -d
```

### 2. Access the Web Dashboard
Open [http://localhost:8000](http://localhost:8000) in your browser.

* **Start/Stop Engine:** One-click controls with real-time parameter tuning.
* **Scan Pairs:** Built-in screener finds newly active high-spread pairs across the exchange.
* **Live Ladder:** Visualizes the order book with our active quotes highlighted inside the spread.
* **Equity & P&L Curve:** Real-time Chart.js telemetry stream via WebSockets.
* **Light / Dark Mode:** Toggle between crisp Light Mode (default) and Dark Mode.
* **Export CSV:** 1-click download of session fills from PostgreSQL for post-trade investigation.

---

## API Endpoints

* `GET /api/status`: Current bot state, parameters, equity, and inventory.
* `POST /api/start`: Starts simulated or live quoting with specified config.
* `POST /api/stop`: Stops quoting immediately and finalizes the DB session.
* `POST /api/reset`: Resets paper capital to $1,000.
* `GET /api/screener`: Scans Hyperliquid for high-spread/high-volume tokens.
* `GET /api/history/sessions`: Retrieves past trading runs stored in PostgreSQL.
* `GET /api/history/fills`: Retrieves execution fills stored in PostgreSQL.
* `GET /api/history/export`: Downloads execution fills as a CSV for external analysis.
* `WS /ws/live`: Real-time bi-directional telemetry broadcast at 4Hz.

---

## Repository Structure

```
├── Dockerfile                  # Production container definition
├── docker-compose.yml          # Container orchestration (FastAPI + PostgreSQL)
├── requirements.txt            # Python dependencies
├── signals.py                  # Avellaneda-Stoikov & OFI mathematics
├── database.py                 # PostgreSQL async persistence layer & schema
├── trader.py                   # Live trading state machine & WebSocket listener
├── server.py                   # FastAPI REST & WebSocket server
└── web/                        # Web Dashboard frontend
    ├── index.html              # High-contrast trading UI
    ├── styles.css              # Modern light & dark theme styling
    └── app.js                  # WebSocket client & Chart.js logic
```

## License
MIT License

