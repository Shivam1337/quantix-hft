import json

import httpx
from app.config import Settings
from app.main import create_app
from app.services.wallet import WalletService
from conftest import FixtureExchangeService

PRIVATE_KEY = "0x4c0883a69102937d6231471b5dbb6204fe5129617082797d32c9d6e9c9f7b7c7"
ADDRESS = "0x8b8E7b858E243F4B82bA6d03E87b4e8AaeB862B9"


async def balance_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.hyperliquid.xyz":
        body = json.loads(request.content)
        if body["type"] == "clearinghouseState":
            return httpx.Response(
                200,
                json={"marginSummary": {"accountValue": "123.45"}, "withdrawable": "100.00"},
            )
        return httpx.Response(
            200,
            json={"balances": [{"coin": "USDC", "total": "25.5", "hold": "1.5"}]},
        )
    if request.url.host == "mainnet.zklighter.elliot.ai":
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "account_type": 0,
                        "collateral": "456.78",
                        "available_balance": "400.00",
                        "assets": [
                            {"symbol": "USDC", "balance": "456.78", "locked_balance": "56.78"}
                        ],
                    }
                ]
            },
        )
    return httpx.Response(404)


async def test_wallet_import_refreshes_read_only_exchange_balances(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'wallet.db'}",
        redis_url="memory://",
        enable_scheduler=False,
        symbols=["BTC-PERP", "ETH-PERP"],
    )
    balance_client = httpx.AsyncClient(transport=httpx.MockTransport(balance_handler))
    wallet = WalletService(client=balance_client)
    app = create_app(
        settings,
        exchange_service=FixtureExchangeService(settings.symbols),
        wallet_service=wallet,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            initial = await client.get("/api/v1/wallet")
            assert initial.status_code == 200
            assert initial.json()["connected"] is False

            invalid = await client.post("/api/v1/wallet/import", json={"private_key": "0x1"})
            assert invalid.status_code == 400

            imported = await client.post(
                "/api/v1/wallet/import", json={"private_key": PRIVATE_KEY}
            )
            assert imported.status_code == 200
            data = imported.json()
            assert data["connected"] is True
            assert data["address"] == ADDRESS
            assert data["live_trading_enabled"] is False
            assert "private_key" not in imported.text

            balances = {item["exchange_id"]: item for item in data["balances"]}
            assert balances["hyperliquid"]["total_usd"] == 123.45
            assert balances["hyperliquid"]["assets"][0]["available"] == 24.0
            assert balances["lighter"]["available_usd"] == 400.0
            assert balances["aevo"]["status"] == "not_configured"

            removed = await client.delete("/api/v1/wallet")
            assert removed.status_code == 200
            assert removed.json()["connected"] is False
