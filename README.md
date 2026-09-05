# Polymarket Real-Time Arbitrage Engine & Simulation Platform

A production-grade, containerized real-time algorithmic trading system for **Polymarket Combinatorial & NegRisk Arbitrage**, engineered for small capital ($50 bankroll) with zero financial risk.

---

## Features

* **Real Market Data Ingestion:** Streams real-time live order books (bids, asks, depths) directly from Polymarket's Gamma and CLOB APIs.
* **Exact Real Fee Modeling:** Enforces real Polygon L2 gas costs (~$0.003–$0.005 per execution) and Polymarket fee schedules (`app/fee_model.py`).
* **Simulated Execution ($50 Bankroll):** Only trades and capital balances are virtual. Executes dynamic rebalancing arbitrage: enters underpriced baskets ($\sum \text{Ask} < 1.00$) and automatically exits as prices normalize toward \$1.00.
* **Persistent Database (PostgreSQL):** Stores events, tokens, price ticks, arbitrage opportunities, positions, trade logs, and portfolio equity snapshots.
* **Read-Only SQL Query Endpoint:** Exposes `POST /api/query` allowing arbitrary `SELECT` queries against the live database with strict mutation safety guards (`DROP`, `INSERT`, `UPDATE` blocked).
* **Interactive Web Dashboard:** Modern dark-mode UI with live equity curve, real-time arbitrage scanner table, open positions, recent trade fills, and an embedded SQL query console.
* **Dual Deployment:** Run containerized via `docker compose` or locally via `.venv`.

---

## Quick Start

### Option 1: Docker Compose (Recommended)

1. Ensure Docker Desktop is running.
2. Launch the full stack (PostgreSQL + FastAPI Engine):
   ```bash
   docker compose up --build -d
   ```
3. Open your browser and navigate to:
   ```
   http://localhost:8000
   ```
4. Stop the stack:
   ```bash
   docker compose down
   ```

---

### Option 2: Local Virtual Environment (`.venv`)

1. Activate the local virtual environment:
   * **Windows (PowerShell):**
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
   * **Linux / macOS:**
     ```bash
     source .venv/bin/activate
     ```

2. Run the application:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   *(If PostgreSQL is not running locally, the system automatically falls back to local SQLite persistence without crashing).*

3. Run automated integration tests:
   ```bash
   python test_system.py
   ```

---

## API Documentation

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Interactive Web Dashboard |
| `GET` | `/api/status` | Engine uptime, database type, and monitored events count |
| `GET` | `/api/portfolio` | Live portfolio summary (Cash, Locked, Total Equity, PnL, Win Rate) |
| `GET` | `/api/opportunities` | Real-time list of scanned multi-outcome baskets & spreads |
| `GET` | `/api/positions` | Currently open simulated positions |
| `GET` | `/api/trades` | Historical trade fills log |
| `POST` | `/api/query` | Secure Read-Only SQL query runner (accepts any `SELECT`) |
| `POST` | `/api/control` | Control engine (`pause`, `resume`, `reset`, `set_threshold`) |
| `GET` | `/api/stream` | Server-Sent Events (SSE) streaming live dashboard updates |

---

### Read-Only SQL Endpoint Examples

You can run any read-only query using `curl` or from the Web Dashboard SQL console:

**1. Inspect Recent Arbitrage Signals:**
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT event_title, basket_sum, gross_spread, net_spread, actionable, created_at FROM arb_opportunities ORDER BY id DESC LIMIT 5;"}'
```

**2. Check Portfolio History:**
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT cash, locked_capital, total_equity, total_pnl, recorded_at FROM portfolio_history ORDER BY id DESC LIMIT 5;"}'
```

**3. Inspect Open Virtual Positions:**
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT id, event_title, entry_basket, shares, cost, status FROM simulated_positions WHERE status = '\''OPEN'\'';"}'
```
