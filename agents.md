# Agent Guidelines & Codebase Architecture Standards

Welcome to the **Quantix HFT** repository. All AI agents and developers contributing to this codebase must adhere strictly to the following architectural, formatting, and structural guidelines.

---

## 1. File Size Limit: Maximum ~250 Lines of Code (LOC)

### Rule
**No file should exceed approximately 250 lines of code.**

### Rationale
- **Maintainability & Debugging**: Smaller files isolate responsibilities, make stack traces straightforward, and simplify pinpointing root causes during active trading.
- **Cognitive Load & Reviewability**: Focused modules make peer and agent reviews faster and prevent accidental regressions.
- **Context Efficiency**: Compact files allow AI agents to view, reason about, and edit code within limited context windows without truncation.

---

## 2. Refactoring & Modular Decomposition Strategy

Whenever an existing file approaches or exceeds ~250 LOC, or when adding a substantial new feature:

### Decomposition Pattern
1. **Create a Dedicated Subpackage / Folder**:
   - Convert a large single module (e.g. `module.py`) into a package directory (e.g. `module/`).
   - Use `__init__.py` to re-export primary public classes, functions, and constants to ensure full backward compatibility with existing imports.

2. **Split by Cohesive Responsibility**:
   - **`models.py` / `types.py`**: Dataclasses, Pydantic schemas, enums, type definitions.
   - **`storage.py` / `db.py`**: Database queries, table definitions, CRUD operations, persistence logic.
   - **`client.py` / `connection.py`**: WebSocket / HTTP networking, protocol serialization/deserialization.
   - **`engine.py` / `service.py`**: Core mathematical, analytical, or state-machine business logic.
   - **`helpers.py` / `utils.py`**: Pure utility functions, formatters, unit converters.

3. **Decouple Concerns**:
   - Separate network transport from pricing/trading logic.
   - Keep synchronous in-memory state distinct from asynchronous persistence I/O.
   - Avoid circular imports by depending on lower-level abstractions or interfaces.

---

## 3. High-Frequency Trading & Systems Engineering Rules

1. **Zero Disk Plaintext Secrets**:
   - Never write credentials, private keys, or API tokens to plaintext JSON or disk files.
   - All state, settings, and credentials must reside in database tables (`wallet_credentials`, `system_settings` in PostgreSQL/SQLite).

2. **Non-Blocking Asynchronous Concurrency**:
   - Market data feeds (Binance, Bybit, OKX, Hyperliquid, Polymarket, Lighter) run concurrently on `asyncio`.
   - Never execute blocking synchronous network calls or heavy CPU-bound tasks inside WebSocket callbacks.
   - Use non-blocking queues or database batch writers for telemetry persistence.

3. **Dual-Backend Resilience (PostgreSQL & SQLite)**:
   - Production defaults to PostgreSQL (`asyncpg`).
   - Local development and test environments gracefully fall back to SQLite WAL mode if PostgreSQL is unavailable.
   - Ensure all schemas and query patterns are compatible with both backends.

4. **Rigorous Verification & Testing**:
   - Every newly created or refactored module must include corresponding unit tests in `tests/`.
   - Run `python -m unittest discover tests` after every modification. All tests must pass with 0 failures before considering a task complete.
