import pytest


@pytest.mark.asyncio
async def test_diagnostics_postgres_get(client):
    response = await client.get("/api/v1/diagnostics/postgres")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "latency_ms" in data
    assert data["latency_ms"] >= 0
    assert "dialect" in data
    assert "table_counts" in data


@pytest.mark.asyncio
async def test_diagnostics_postgres_query_valid(client):
    # Default query
    resp = await client.post("/api/v1/diagnostics/postgres", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["columns"] == ["ping"]
    assert data["rows"] == [[1]]
    assert data["row_count"] == 1

    # Custom read-only select
    resp2 = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "SELECT count(*) as total FROM system_settings"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ok"
    assert data2["columns"] == ["total"]
    assert data2["row_count"] == 1

    # With CTE
    resp3 = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "WITH sample AS (SELECT 42 as num) SELECT num FROM sample"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["rows"] == [[42]]


@pytest.mark.asyncio
async def test_diagnostics_postgres_query_rejects_mutations(client):
    # Reject DELETE
    resp = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "DELETE FROM system_settings"},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()

    # Reject DROP
    resp = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "DROP TABLE system_settings"},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()

    # Reject INSERT
    resp = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "INSERT INTO system_settings DEFAULT VALUES"},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()

    # Reject multiple statements
    resp = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "SELECT 1; SELECT 2"},
    )
    assert resp.status_code == 400
    assert "multiple" in resp.json()["detail"].lower()

    # Reject sneaky mutation inside select
    resp = await client.post(
        "/api/v1/diagnostics/postgres",
        json={"query": "SELECT * FROM system_settings WHERE id = (DELETE FROM system_settings)"},
    )
    assert resp.status_code == 400
    assert "mutating" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_diagnostics_redis_get(client):
    response = await client.get("/api/v1/diagnostics/redis")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["ping"] == "PONG"
    assert "latency_ms" in data
    assert "mode" in data
    assert "key_count" in data


@pytest.mark.asyncio
async def test_diagnostics_redis_query_valid(client):
    # PING
    resp = await client.post("/api/v1/diagnostics/redis", json={"command": "PING"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["result"] == "PONG"

    # ECHO
    resp = await client.post(
        "/api/v1/diagnostics/redis",
        json={"command": "ECHO", "args": ["diagnostics_test"]},
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == "diagnostics_test"

    # DBSIZE
    resp = await client.post("/api/v1/diagnostics/redis", json={"command": "DBSIZE"})
    assert resp.status_code == 200
    assert isinstance(resp.json()["result"], int)

    # KEYS
    resp = await client.post(
        "/api/v1/diagnostics/redis",
        json={"command": "KEYS", "args": ["*"]},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["result"], list)


@pytest.mark.asyncio
async def test_diagnostics_redis_query_rejects_mutations(client):
    # Reject FLUSHALL
    resp = await client.post(
        "/api/v1/diagnostics/redis",
        json={"command": "FLUSHALL"},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()

    # Reject SET
    resp = await client.post(
        "/api/v1/diagnostics/redis",
        json={"command": "SET", "args": ["key", "val"]},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()

    # Reject DEL
    resp = await client.post(
        "/api/v1/diagnostics/redis",
        json={"command": "DEL", "args": ["key"]},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"].lower()
