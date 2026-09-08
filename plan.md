# System Architecture & Implementation Plan: Cross-Exchange Funding Arbitrage Platform

A production-grade, delta-neutral funding rate arbitrage platform across **Hyperliquid**, **Aevo**, and **Lighter.xyz** featuring an asynchronous Python backend and a React-based management dashboard.

---

## 1. System Architecture Overview

```
[ Exchanges: Hyperliquid | Aevo | Lighter.xyz ]
                     │ (REST / WebSockets)
                     ▼
         ┌────────────────────────┐
         │     FastAPI Backend    │
         │  - Market Data Engine  │
         │  - Arb Calculator      │
         │  - Risk & Unwind Engine│
         │  - Order Execution Hub │
         └───────────┬────────────┘
                     │ (WebSocket & REST API)
                     ▼
         ┌────────────────────────┐
         │     React Frontend     │
         │  - Opportunity Radar   │
         │  - Active Positions UI │
         │  - One-Click Execution │
         │  - Flip Alerts & Logs  │
         └────────────────────────┘
```

---

## 2. Component Specifications

### 2.1 Backend Core (Python / FastAPI)
* **Market Ingestion Service**: Polls and streams funding rates, mark prices, orderbooks, and open interest from Hyperliquid, Aevo, and Lighter.xyz with rate-limit pacing and 1h normalization.
* **Arbitrage Matrix Engine**: Computes pairwise delta-neutral spreads, Net APR, basis divergence, capacity bottlenecks, and venue-specific fee breakeven (HL maker 1.5 bps, Lighter 0%).
* **Risk & Auto-Unwind Engine**:
  * Tracks live positions and net accrued funding.
  * **Funding Flip Guard**: Automatically unwinds or alerts if Net APR remains negative for $\ge 2$ consecutive funding hours.
  * **Basis Divergence Guard**: Alerts if exchange mark price basis expands beyond safety threshold.
* **Order Execution Manager**: Routes paired orders (Maker limit on HL $\rightarrow$ instantaneous market hedge on Lighter).
* **WebSocket Server**: Pushes real-time rate updates and position status to the frontend.

### 2.2 Frontend Dashboard (React + Vite + Tailwind CSS)
* **Live Opportunity Radar**:
  * Filterable table by Min APR, Min Open Interest, and selected exchange pairs.
  * Leg breakdown: Visual green/red tags for received vs paid funding rates.
  * Dynamic Breakeven and Capacity metrics.
* **Position & Portfolio Manager**:
  * Live active arbitrage positions with unrealized funding profit, price basis PnL, and duration.
  * Emergency "Close Position" button (closes both legs atomically).
* **Trade Simulator / Sizer**:
  * Enter capital size (e.g. \$1,000) $\rightarrow$ calculates projected hourly cashflow, monthly return, and fee breakeven time.
* **Settings & Alert Center**:
  * Configurable alert webhooks (Telegram/Discord), fee tier overrides, and auto-unwind rules.

---

## 3. Database & State Management
* **PostgreSQL**: Stores trade logs, position history, fee settings, and historical funding rates.
* **In-Memory Cache (Redis / Dict)**: High-frequency live orderbook and ticker caches.

---

## 4. Phased Implementation Roadmap

1. **Phase 1 (Backend API & WebSocket)**: Wrap existing scanner into a FastAPI service broadcasting live rates via WebSocket.
2. **Phase 2 (React Frontend Setup)**: Scaffold Vite + React + Tailwind dashboard with live opportunity table and trade calculator.
3. **Phase 3 (Position & Risk Tracker)**: Mock and live position management, flip detection, and alert dispatchers.
4. **Phase 4 (Execution & Wallet Integration)**: API wallet/signer integration for automated paired execution.
