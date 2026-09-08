import pytest


@pytest.mark.asyncio
async def test_system_metrics_endpoint(client):
    response = await client.get("/api/v1/system/metrics")
    assert response.status_code == 200
    data = response.json()

    assert "system_cpu_pct" in data
    assert isinstance(data["system_cpu_pct"], (int, float))
    assert 0 <= data["system_cpu_pct"] <= 100

    assert "system_ram_pct" in data
    assert 0 <= data["system_ram_pct"] <= 100
    assert data["system_ram_total_gb"] > 0
    assert data["system_ram_used_gb"] >= 0

    assert "system_disk_pct" in data
    assert 0 <= data["system_disk_pct"] <= 100
    assert data["system_disk_total_gb"] > 0

    assert "process_cpu_pct" in data
    assert data["process_cpu_pct"] >= 0
    assert data["process_ram_mb"] > 0

    assert "postgres_size_human" in data
    assert isinstance(data["postgres_size_human"], str)

    assert "redis_size_human" in data
    assert isinstance(data["redis_size_human"], str)

    assert "timestamp" in data
