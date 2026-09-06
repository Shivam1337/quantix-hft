# BTC Perpetual Lead-Lag Measurement Experiment

This project is a paper-only experiment for the hypothesis that Lighter's BTC
perpetual executable quote sometimes follows a confirmed move on major venues.
It does not place orders or establish a profitable trading edge.

## Deploy the complete system with Docker Compose

Docker Compose is the supported deployment path. It starts the paper-only app
and PostgreSQL together, keeps both host ports local-only, and stores derived
experiment state in the named PostgreSQL volume.

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8765/api/system/readiness
```

For a normal continuous deployment, rebuild and replace only the app service:

```powershell
docker compose up -d --build app
```

`app` receives `SIGTERM` and has 35 seconds to stop: up to 5 seconds for
HTTP/SSE clients to release, then up to 25 seconds for PostgreSQL draining.
The application stops feed intake first, cancels feed tasks, closes its HTTP
session, drains the derived PostgreSQL queue, and then exits. Closed paper
trades and chart samples survive in PostgreSQL and are restored at the next
start. An open paper position is recorded as a `PROCESS_SHUTDOWN` audit event
instead of inventing an exit price or realized PnL, so deployment cannot inflate
or corrupt performance.

Use these commands to inspect or stop the deployment:

```powershell
docker compose logs --tail=200 app
docker compose stop app
docker compose down
```

`docker compose down` preserves the named PostgreSQL volume. Do not use
`docker compose down -v` unless you intentionally want to erase all persisted
derived experiment state.

The Compose credentials are development-only and the exposed ports bind to
`127.0.0.1`; replace the database password and network policy before any
non-local deployment.

## Run from source locally

```powershell
docker compose up -d postgres
python .\run_server.py
```

PostgreSQL binds only to `127.0.0.1:5432`; the server binds to `127.0.0.1:8765`.
Both use local-only defaults. The server does not open a browser.
Use terminal HTTP requests to inspect it, for example:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/system/health
Invoke-RestMethod http://127.0.0.1:8765/api/system/providers
Invoke-RestMethod http://127.0.0.1:8765/api/system/resources
Invoke-RestMethod http://127.0.0.1:8765/api/system/persistence
Invoke-RestMethod http://127.0.0.1:8765/api/analytics/experiment-status
```

The dashboard uses a local canvas chart renderer, so charts do not depend on a
third-party CDN. It records a gap-aware sample at most every 250 ms: a provider
that has not produced a fresh quote is shown as a gap, never as a false zero.

`/api/system/resources` reports host CPU/RAM beside the server process CPU/RAM.
Both CPU percentages are normalized to total logical-machine capacity, so they
can be compared directly. `/api/system/providers` exposes every provider's role,
freshness, update count, price/spread, and available two-second movement.

## PostgreSQL persistence

The application persists only derived state: rate-limited chart samples, decision
transitions, lead-lag events, and closed paper trades. It does **not** save raw
WebSocket messages, full order-book updates, or every incoming quote. This means
the dashboard restores recent charts and paper-trade history after a restart,
without generating multi-gigabyte raw-message logs.

Inspect the stored debug data from the terminal:

```powershell
python .\inspect_postgres.py
```

## Measurement rules

- Binance, Bybit, OKX, and Hyperliquid are the primary discovery venues.
- Polymarket remains a recorded comparison feed but cannot nominate a signal.
- A paper signal requires at least three fresh major venues moving together.
- New runs never write raw incoming WebSocket messages, full order-book updates,
  or every individual quote. Only bounded derived state is persisted.
- A closed spread is classified as `LIGHTER_CATCHUP`, `LEADER_REVERSAL`,
  `BASIS_SHIFT`, or `MIXED_MOVE`; it is not automatically counted as a catch-up.

## Review evidence

The historical `analyze_experiment.py` helper can still analyze legacy JSONL
archives, but new runs do not create those raw-message files. Inspect current
derived state with PostgreSQL instead:

```powershell
python .\inspect_postgres.py
```

Do not evaluate the hypothesis from a few events or from paper PnL alone. Review
at least 200 completed, high-consensus events across market conditions, and require
positive out-of-sample results after realistic fills, latency, funding, and impact.
